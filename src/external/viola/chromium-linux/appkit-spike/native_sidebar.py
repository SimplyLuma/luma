# SPDX-License-Identifier: GPL-3.0-only
"""Native sidebar presentation over Viola's retained organization state.

Owns widgets only. Mutations and native drag/drop use browser organization commands.
"""
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk, Pango
from luma_appkit import add_style_sheet
from native_footer import NativeFooter
from luma_appkit.content_cards import Card
from workspace_colors import set_theme


def reconcile(box, desired):
    previous = None
    for widget in desired:
        parent = widget.get_parent()
        if parent is not box:
            if parent:
                parent.remove(widget)
            box.insert_child_after(widget, previous)
        elif widget.get_prev_sibling() is not previous:
            box.reorder_child_after(widget, previous)
        previous = widget
    child = box.get_first_child()
    while child:
        following = child.get_next_sibling()
        if child not in desired:
            box.remove(child)
        child = following


class NativeSidebar(Gtk.Box):
    def __init__(self, host):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, width_request=252, hexpand=False)
        self.host = host
        self.state = {}
        self.rows, self.favorites, self.sections, self.groups = {}, {}, {}, {}
        self.collapsed_groups = set()
        self.seen_groups = set()
        self.workspace_editor = None
        self.add_css_class('viola-sidebar')
        self.provider = add_style_sheet(
            str(Path(__file__).with_name('native_sidebar.css')),
            priority=Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        from sidebar_preferences import SidebarPreferences
        self.preferences = SidebarPreferences(self)
        self.favorite_grid = Gtk.Grid(column_homogeneous=True, column_spacing=8, row_spacing=8)
        # Retain four equal columns when only one or two pages are pinned.
        for column in range(4):
            self.favorite_grid.attach(Gtk.Box(can_target=False), column, 0, 1, 1)
        self.append(self.favorite_grid)
        self.favorite_hint = None
        from native_reorder import NativeReorder
        self.reorder = NativeReorder(self)
        self.collections = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.new_tab_row = Gtk.Button()
        self.new_tab_row.add_css_class('viola-new-tab')
        new_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        new_line.append(Gtk.Image(icon_name='luma-plus-symbolic', pixel_size=16))
        new_line.append(Gtk.Label(label='New tab', xalign=0))
        self.new_tab_row.set_child(new_line)
        self.new_tab_row.connect('clicked', lambda *_: host._new_tab())
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                           vexpand=True, child=self.collections)
        self.append(self.scroller)
        self.footer = NativeFooter(host)
        self.append(self.footer)
        self.switcher = Gtk.Button()
        self.switcher.add_css_class('viola-workspace-switcher')
        self.switcher.add_css_class('flat')
        switcher_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        switcher_line.append(Gtk.Image(icon_name='viola-layers-symbolic', pixel_size=14))
        self.switcher_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        switcher_line.append(self.switcher_label)
        switcher_line.append(Gtk.Image(icon_name='luma-chevron-down-symbolic', pixel_size=12))
        self.switcher.set_child(switcher_line)
        self.switcher.connect('clicked', self.open_workspace_picker)
        self.workspace_well = Card(self.switcher, padded=False, recessed=True)
        self.append(self.workspace_well)

    def open_workspace_picker(self, *_):
        scopes = self.state.get('workspaceScopes') or self.state.get('spaces', [])
        active = self.state.get('workspaceLensId') or self.state.get('activeSpaceId', '')
        spaces = {space['id']: space for space in self.state.get('spaces', [])}
        items = [dict(index=0, kind='title', label='WORKSPACES', visible=True, enabled=False)]
        actions = {}
        for index, scope in enumerate(scopes, 1):
            items.append(dict(index=index, kind='check',
                label='All workspaces' if scope.get('isAll') else scope['name'],
                checked=scope['id'] == active, visible=True, enabled=True,
                theme=None if scope.get('isAll') else spaces.get(scope['id'], scope).get('theme', 'sky')))
            actions[index] = lambda k=scope['id']: self.host.send('space:switch', {'spaceId': k})
        index = len(items)
        items.extend([dict(index=index, kind='separator', label='', visible=True, enabled=False),
            dict(index=index+1, kind='item', label='New workspace…', visible=True, enabled=True),
            dict(index=index+2, kind='item', label='Edit workspaces…', visible=True, enabled=True)])
        actions[index+1] = self.create_workspace
        actions[index+2] = self.edit_workspaces
        menu = self.host.show_menu(self.switcher, {'nonce': 'workspace-picker', 'items': items},
                                  local_activate=lambda _, path: actions[path[0]](), position=Gtk.PositionType.TOP)
        if menu:
            for index, scope in enumerate(scopes, 1):
                if scope.get('isAll'):
                    continue
                button = next((widget for _, widget in menu._placed if isinstance(widget, Gtk.Button)
                               and widget.get_action_name() == 'menu.item-' + str(index)), None)
                if button:
                    self.reorder.workspace(button, scope['id'])
            self.switcher.add_css_class('active')
            menu.connect('closed', lambda *_: self.switcher.remove_css_class('active'))

    def edit_workspaces(self):
        from workspace_editor import WorkspaceEditor
        dialog = getattr(self, 'settings_dialog', None)
        if dialog is None:
            dialog = WorkspaceEditor(self.host)
            self.settings_dialog = dialog
            dialog.connect('closed', lambda *_: setattr(self, 'settings_dialog', None))
        dialog.present(self.host)

    def create_workspace(self):
        if self.workspace_editor:
            self.workspace_editor.grab_focus()
            return
        entry = Gtk.Entry(placeholder_text='Workspace name')
        self.workspace_editor = entry
        entry.add_css_class('luma-inline-rename')
        self.collections.prepend(entry)
        complete = False
        def finish(save):
            nonlocal complete
            if complete:
                return
            complete = True
            self.workspace_editor = None
            name = entry.get_text().strip()
            if entry.get_parent():
                self.collections.remove(entry)
            if save and name:
                self.host.send('space:create', {'name': name, 'icon': '◫'})
        entry.connect('activate', lambda *_: finish(True))
        keys = Gtk.EventControllerKey()
        def pressed(_controller, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                finish(False)
                return True
            return False
        keys.connect('key-pressed', pressed)
        entry.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect('leave', lambda *_: finish(True))
        entry.add_controller(focus)
        entry.grab_focus()

    def disclosure(self, workspace=False):
        button = Gtk.Button()
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button.identity = Gtk.Box(width_request=8, height_request=8, valign=Gtk.Align.CENTER) if workspace else Gtk.Image(
            icon_name='viola-square-stack-symbolic', pixel_size=14)
        button.identity.add_css_class('viola-workspace-dot' if workspace else 'viola-group-icon')
        line.append(button.identity)
        button.name = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        line.append(button.name)
        button.count = Gtk.Label()
        button.count.add_css_class('viola-group-count')
        button.count.set_visible(not workspace)
        line.append(button.count)
        button.arrow = Gtk.Image(icon_name='luma-chevron-down-symbolic', pixel_size=12)
        line.append(button.arrow)
        button.set_child(line)
        return button

    def context(self, widget, channel, payload):
        gesture = Gtk.GestureClick(button=3)
        gesture.connect('pressed', lambda _g, _n, x, y: self.host.open_retained_menu(
            widget, channel, payload(), x=x, y=y))
        widget.add_controller(gesture)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        def context_key(_controller, key, _code, state):
            modifiers = state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK
                                 | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK)
            if key == Gdk.KEY_Menu or (key == Gdk.KEY_F10 and modifiers == Gdk.ModifierType.SHIFT_MASK):
                self.host.open_retained_menu(widget, channel, payload())
                return True
            return False
        keys.connect('key-pressed', context_key)
        widget.add_controller(keys)
        touch = Gtk.GestureLongPress(touch_only=True)
        def held(gesture, x, y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.host.open_retained_menu(widget, channel, payload(), x=x, y=y)
        touch.connect('pressed', held)
        widget.add_controller(touch)

    def tab_widget(self, tab, favorite=False):
        pool = self.favorites if favorite else self.rows
        key = tab['id']
        widget = pool.get(key)
        if widget is None:
            widget = Card(padded=False) if favorite else Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            widget.add_css_class('viola-favorite' if favorite else 'viola-tab')
            widget.tab_id = key
            widget.activate = Gtk.Button(hexpand=True)
            widget.activate.add_css_class('viola-tab-activate')
            widget.activate.add_css_class('flat')
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            if favorite:
                content.set_halign(Gtk.Align.CENTER)
            widget.icon = Gtk.Image(pixel_size=20 if favorite else 16)
            # A neutral page glyph is used only until the browser-owned
            # favicon service supplies the actual icon; never a letter tile.
            widget.icon.set_from_icon_name('text-x-generic-symbolic')
            widget.icon.add_css_class('viola-tab-favicon')
            content.append(widget.icon)
            widget.label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            widget.label.set_visible(not favorite)
            content.append(widget.label)
            widget.elsewhere = Gtk.Image(icon_name='window-new-symbolic', pixel_size=14,
                                         tooltip_text='Viewing in another window')
            widget.elsewhere.add_css_class('viola-tab-elsewhere')
            if favorite:
                content.remove(widget.icon)
                badge = Gtk.Overlay(child=widget.icon)
                widget.elsewhere.set_halign(Gtk.Align.END)
                widget.elsewhere.set_valign(Gtk.Align.END)
                widget.elsewhere.set_pixel_size(10)
                badge.add_overlay(widget.elsewhere)
                content.prepend(badge)
            else:
                content.append(widget.elsewhere)
            widget.activate.set_child(content)
            widget.activate.connect('clicked', lambda *_: self.host.send('tab:activate', {'tabId': key}))
            widget.append(widget.activate)
            if not favorite:
                widget.close = Gtk.Button(icon_name='window-close-symbolic', tooltip_text='Close tab')
                widget.close.add_css_class('viola-tab-close')
                widget.close.connect('clicked', lambda *_: self.host.send('tab:close', {'tabId': key}))
                widget.append(widget.close)
            self.context(widget, 'tab:menu', lambda: {
                'tabId': key, 'section': widget.section, 'selectedTabIds': [key]})
            self.reorder.tab(widget)
            pool[key] = widget
        widget.source_space_id = tab.get('sourceSpaceId') or self.state.get('activeSpaceId')
        widget.section = ('favorite' if favorite else 'pinned'
                          if any(t['id'] == key for t in self.state.get('pinned', [])) else 'today')
        title = tab.get('title') or tab.get('url') or 'New tab'
        url = tab.get('url') or 'about:blank'
        icon_key = (url, tab.get('favicon', ''), bool(tab.get('loading')))
        if getattr(widget, 'icon_key', None) != icon_key and self.host.favicons:
            widget.icon_key = icon_key
            def loaded(texture):
                if widget.icon_key == icon_key and texture:
                    widget.icon.set_from_paintable(texture)
            self.host.favicons.request(url, loaded, revision=icon_key[1:])
        widget.label.set_label(title)
        elsewhere = bool(tab.get('viewedElsewhere'))
        widget.elsewhere.set_visible(elsewhere)
        widget.activate.set_tooltip_text(title + (' — Viewing in another window; click to move here' if elsewhere else ''))
        states = [title, tab.get('sourceSpaceName', '')]
        if key == self.state.get('activeTabId'):
            states.append('current tab')
        states.extend(word for condition, word in ((tab.get('pinned'), 'pinned'),
            (tab.get('muted'), 'muted'), (tab.get('discarded'), 'sleeping'),
            (tab.get('audible'), 'playing audio'),
            (elsewhere, 'viewing in another window; activate to move here')) if condition)
        widget.activate.update_property([Gtk.AccessibleProperty.LABEL], [', '.join(filter(None, states))])
        selected = key == self.state.get('activeTabId')
        (widget.add_css_class if selected else widget.remove_css_class)('lumaui-selected')
        for name, enabled in (('active', selected),
                              ('sleeping', tab.get('discarded', False)),
                              ('viewed-elsewhere', elsewhere)):
            (widget.add_css_class if enabled else widget.remove_css_class)(name)
        return widget

    def render_favorite_drop_zone(self):
        empty = not self.state.get('favorites')
        show_hint = empty and self.reorder.dragging
        if show_hint and self.favorite_hint is None:
            self.favorite_hint = Gtk.Label(label='Drop in Favorites', height_request=42)
            self.favorite_hint.add_css_class('dim-label')
            self.favorite_grid.attach(self.favorite_hint, 0, 0, 4, 1)
        elif not show_hint and self.favorite_hint is not None:
            self.favorite_grid.remove(self.favorite_hint)
            self.favorite_hint = None
        self.favorite_grid.set_visible(not empty or show_hint)

    def toggle_workspace(self, key):
        collapsed = set(self.state.get('collapsedWorkspaceSections', []))
        collapsed.symmetric_difference_update([key])
        self.host.send('sidebar:collapsedWorkspaceSections', {'spaceIds': sorted(collapsed)})

    def toggle_group(self, key):
        group = next(g for g in self.state.get('tabGroups', []) if g['id'] == key)
        if group.get('keepExpanded'):
            return
        expanded = self.groups[key].content.get_visible()
        if expanded:
            self.collapsed_groups.add(key)
        else:
            self.collapsed_groups.discard(key)
        self.host.send('sidebar:collapsedGroups', {'groupIds': sorted(self.collapsed_groups)})
        if not expanded:
            if group.get('saved'):
                self.host.send('tabGroup:openSaved', {'groupId': key, 'spaceId': group.get('spaceId')})
            elif group.get('tabIds') and self.state.get('activeTabId') not in group['tabIds']:
                self.host.send('tab:activate', {'tabId': group['tabIds'][0]})
        self.render(self.state)

    def group_widget(self, group, lookup):
        key = group['id']
        widget = self.groups.get(key)
        if widget is None:
            widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            widget.add_css_class('viola-tab-group')
            widget.header = self.disclosure()
            widget.header.add_css_class('viola-group-header')
            widget.header.connect('clicked', lambda *_: self.toggle_group(key))
            widget.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            widget.append(widget.header)
            widget.append(widget.content)
            self.context(widget.header, 'tabGroup:menu', lambda: {
                'groupId': key, 'spaceId': next((g.get('spaceId') for g in self.state.get('tabGroups', []) if g['id'] == key), None)})
            self.groups[key] = widget
        if key not in self.seen_groups:
            self.seen_groups.add(key)
            if group.get('initiallyCollapsed'):
                self.collapsed_groups.add(key)
        active = self.state.get('activeTabId') in group.get('tabIds', [])
        expanded = group.get('keepExpanded') or (active and key not in self.collapsed_groups)
        children = [self.tab_widget(lookup[key]) for key in group.get('tabIds', []) if key in lookup]
        reconcile(widget.content, children)
        widget.content.set_visible(bool(expanded))
        (widget.add_css_class if expanded else widget.remove_css_class)('expanded')
        count = group.get('savedTabCount', len(children)) if group.get('saved') else len(children)
        widget.header.name.set_label(group.get('title', 'Tab group'))
        widget.header.count.set_label(str(count))
        widget.header.arrow.set_from_icon_name('luma-chevron-down-symbolic' if expanded else 'viola-chevron-right-symbolic')
        widget.header.update_state([Gtk.AccessibleState.EXPANDED], [int(bool(expanded))])
        return widget

    def render(self, state):
        self.state = state
        settings = state.get('settings') or {}
        geometry = self.preferences.apply(settings)
        continuous = state.get('isAllWorkspaces') and settings.get('allWorkspacesGroupByWorkspace') is False
        nested = settings.get('otherWindowTabsLayout') == 'nested'
        self.collections.set_spacing(geometry['tabSpacing'] if continuous else geometry['workspaceSectionSpacing'])
        self.footer.render(state)
        tabs = state.get('pinned', []) + state.get('today', [])
        lookup = {tab['id']: tab for tab in tabs}
        groups = state.get('tabGroups', [])
        grouped = {key for group in groups for key in group.get('tabIds', [])}
        favorite_ids = {tab['id'] for tab in state.get('favorites', [])}
        for key, widget in list(self.favorites.items()):
            if key not in favorite_ids:
                self.favorite_grid.remove(widget)
                del self.favorites[key]
        if favorite_ids and self.favorite_hint is not None:
            self.favorite_grid.remove(self.favorite_hint)
            self.favorite_hint = None
        for index, tab in enumerate(state.get('favorites', [])):
            widget = self.tab_widget(tab, True)
            position = (index % 4, index // 4)
            if getattr(widget, 'grid_position', None) != position:
                if widget.get_parent():
                    self.favorite_grid.remove(widget)
                self.favorite_grid.attach(widget, *position, 1, 1)
                widget.grid_position = position
        self.render_favorite_drop_zone()
        spaces = state.get('spaces', [])
        lens = state.get('workspaceLensId') or state.get('activeSpaceId')
        if not state.get('isAllWorkspaces'):
            spaces = [space for space in spaces if space['id'] == lens]
        if not spaces:
            spaces = [{'id': '', 'name': 'Tabs'}]
        desired = []
        for space in spaces:
            key = space['id']
            section = self.sections.get(key)
            if section is None:
                section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                section.header = self.disclosure(workspace=True)
                section.header.add_css_class('viola-workspace-header')
                section.header.connect('clicked', lambda *_args, k=key: self.toggle_workspace(k))
                section.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                section.divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                section.elsewhere = Gtk.Expander(label='In other windows')
                section.elsewhere_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                section.elsewhere.set_child(section.elsewhere_content)
                section.append(section.header)
                section.append(section.content)
                self.context(section.header, 'tabs:menu', lambda k=key: {'spaceId': k, 'workspaceHeader': True})
                self.reorder.workspace(section.header, key)
                self.reorder.tab_section(section.content, key)
                self.reorder.tab_section(section.header, key)
                self.sections[key] = section
            section.header.name.set_label((space.get('name') or space.get('title') or 'Tabs').upper())
            set_theme(section.header.identity, space.get('theme'))
            section.header.set_visible(not continuous)
            section.content.set_spacing(geometry['tabSpacing'])
            section.content.set_margin_start(0 if continuous else geometry['workspaceContentInset'])
            section.content.set_margin_end(0 if continuous else geometry['workspaceContentInsetRight'])
            section.elsewhere_content.set_spacing(geometry['tabSpacing'])
            collapsed = not continuous and key in state.get('collapsedWorkspaceSections', [])
            section.header.arrow.set_from_icon_name('viola-chevron-right-symbolic' if collapsed else 'luma-chevron-down-symbolic')
            section.header.update_state([Gtk.AccessibleState.EXPANDED], [int(not collapsed)])
            section.content.set_visible(not collapsed)
            items = []
            for kind in ('pinned', 'today'):
                candidates = [tab for tab in state.get(kind, []) if not state.get('isAllWorkspaces')
                              or tab.get('sourceSpaceId') == key]
                hosted = [group for group in groups if group.get('section', 'today') == kind
                          and (not state.get('isAllWorkspaces') or group.get('spaceId') == key)]
                if kind == 'today' and items and (candidates or hosted):
                    items.append(section.divider)
                anchored = {group.get('anchorTabId'): group for group in hosted}
                placed = set()
                for tab in candidates:
                    group = anchored.get(tab['id'])
                    if group and group['id'] not in placed:
                        items.append(self.group_widget(group, lookup))
                        placed.add(group['id'])
                    if tab['id'] not in grouped:
                        items.append(self.tab_widget(tab))
                for group in hosted:
                    if group['id'] not in placed:
                        items.append(self.group_widget(group, lookup))
            elsewhere = []
            if nested:
                for item in items:
                    identity = getattr(item, 'tab_id', None)
                    if identity and lookup.get(identity, {}).get('viewedElsewhere'):
                        elsewhere.append(item)
                    else:
                        group = next((group for group in groups if self.groups.get(group['id']) is item), None)
                        members = [lookup[tab] for tab in (group or {}).get('tabIds', []) if tab in lookup]
                        if members and all(tab.get('viewedElsewhere') for tab in members):
                            elsewhere.append(item)
            items = [item for item in items if item not in elsewhere]
            if items and items[0] is section.divider:
                items.pop(0)
            # Keep groups intact and reuse the exact keyed rows when ownership
            # changes; no active drag source is replaced with a duplicate.
            reconcile(section.elsewhere_content, elsewhere)
            section.elsewhere.set_label(f'In other windows ({len(elsewhere)})')
            if items and items[-1] is section.divider:
                items.pop()
            if elsewhere:
                items.append(section.elsewhere)
            reconcile(section.content, items)
            desired.append(section)
        reconcile(self.collections, ([self.workspace_editor] if self.workspace_editor else [])
                  + desired + [self.new_tab_row])
        for key in list(self.rows):
            if key not in lookup:
                widget = self.rows.pop(key)
                if widget.get_parent():
                    widget.get_parent().remove(widget)
        current = next((s for s in spaces if s['id'] == lens), {})
        if getattr(self, 'settings_dialog', None):
            self.settings_dialog.refresh(state)
        self.switcher_label.set_label('All workspaces' if state.get('isAllWorkspaces')
                                else current.get('name') or current.get('title') or 'Workspaces')
