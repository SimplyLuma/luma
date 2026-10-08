# SPDX-License-Identifier: GPL-3.0-only
"""Apply bounded existing appearance settings to native sidebar content only."""
import math
from gi.repository import Gdk, Gtk

LIMITS = {
    'tabHeight': (28, 48, 34), 'tabSpacing': (0, 10, 3),
    'tabRadius': (4, 16, 9), 'workspaceHeaderHeight': (28, 48, 34),
    'workspaceHeaderRadius': (0, 16, 9), 'workspaceSectionSpacing': (0, 30, 13),
    'workspaceContentInset': (0, 24, 12), 'workspaceContentInsetRight': (0, 24, 12),
}


def bounded(settings):
    result = {}
    for key, (minimum, maximum, default) in LIMITS.items():
        value = settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            value = default
        result[key] = round(max(minimum, min(maximum, value)))
    return result


class SidebarPreferences:
    def __init__(self, sidebar):
        self.sidebar = sidebar
        self.values = None
        self.provider = Gtk.CssProvider()
        self.scope = 'viola-settings-' + str(id(sidebar))
        sidebar.add_css_class(self.scope)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    def apply(self, settings):
        values = bounded(settings)
        if values != self.values:
            self.values = values
            prefix = '.' + self.scope
            css = f'''
{prefix} .viola-tab, {prefix} .viola-group-header {{ min-height: {values['tabHeight']}px; }}
{prefix} .viola-tab {{ border-radius: {values['tabRadius']}px; }}
{prefix} .viola-workspace-header {{ min-height: {values['workspaceHeaderHeight']}px; border-radius: {values['workspaceHeaderRadius']}px; }}
'''
            self.provider.load_from_string(css)
        return values

    def close(self):
        Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self.provider)
