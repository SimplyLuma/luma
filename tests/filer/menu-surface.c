/* SPDX-License-Identifier: Apache-2.0
 * Render the actual GtkPopoverMenu viewport inside Filer's surface hierarchy.
 * The menu owns its paint; generic application viewport rules must not cover it.
 * Run under a private display with: menu-surface style.css light|dark clean|painted
 */
#include <adwaita.h>
#include <string.h>

static GtkWidget *find_viewport(GtkWidget *widget) {
    if (GTK_IS_VIEWPORT(widget)) return widget;
    for (GtkWidget *child = gtk_widget_get_first_child(widget); child;
         child = gtk_widget_get_next_sibling(child)) {
        GtkWidget *found = find_viewport(child);
        if (found) return found;
    }
    return NULL;
}

static gboolean painted(GtkWidget *widget) {
    GtkSnapshot *snapshot = gtk_snapshot_new();
    gtk_snapshot_render_background(snapshot, gtk_widget_get_style_context(widget),
                                   0, 0, 32, 32);
    GskRenderNode *node = gtk_snapshot_free_to_node(snapshot);
    gboolean result = node != NULL;
    if (node) gsk_render_node_unref(node);
    return result;
}

int main(int argc, char **argv) {
    g_assert_cmpint(argc, ==, 4);
    adw_init();
    adw_style_manager_set_color_scheme(adw_style_manager_get_default(),
        strcmp(argv[2], "dark") == 0 ? ADW_COLOR_SCHEME_FORCE_DARK : ADW_COLOR_SCHEME_FORCE_LIGHT);
    GtkCssProvider *tokens = gtk_css_provider_new();
    gtk_css_provider_load_from_path(tokens, strcmp(argv[2], "dark") == 0 ? "tokens-dark.css" : "tokens-light.css");
    gtk_style_context_add_provider_for_display(gdk_display_get_default(), GTK_STYLE_PROVIDER(tokens), GTK_STYLE_PROVIDER_PRIORITY_THEME);
    GtkCssProvider *provider = gtk_css_provider_new();
    gtk_css_provider_load_from_path(provider, argv[1]);
    gtk_style_context_add_provider_for_display(gdk_display_get_default(),
        GTK_STYLE_PROVIDER(provider), GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    for (unsigned sidebar = 0; sidebar < 2; sidebar++) {
        GtkWidget *window = gtk_window_new();
        gtk_widget_add_css_class(window, "luma-files-window");
        GtkWidget *surface = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
        gtk_widget_add_css_class(surface, sidebar ? "luma-sidebar-surface" : "luma-content-surface");
        gtk_window_set_child(GTK_WINDOW(window), surface);
        GMenu *model = g_menu_new();
        g_menu_append(model, "Open", "win.open");
        GtkWidget *menu = gtk_popover_menu_new_from_model(G_MENU_MODEL(model));
        gtk_widget_set_parent(menu, surface);
        gtk_window_present(GTK_WINDOW(window));
        gtk_popover_popup(GTK_POPOVER(menu));
        while (g_main_context_iteration(NULL, FALSE));
        GtkWidget *viewport = find_viewport(menu);
        g_assert_nonnull(viewport);
        g_assert_cmpint(painted(viewport), ==, strcmp(argv[3], "painted") == 0);
        g_assert_true(painted(surface));
        gtk_widget_unparent(menu);
        gtk_window_destroy(GTK_WINDOW(window));
        g_object_unref(model);
    }
    g_object_unref(provider);
    g_print("PASS: %s menu viewport ownership (%s), both Filer surfaces\n", argv[2], argv[3]);
    return 0;
}
