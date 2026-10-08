# SPDX-License-Identifier: GPL-3.0-only
"""Native drag gestures route to Viola's existing organization commands."""
import uuid
import os
from gi.repository import Gdk, GLib, Gtk

# Tokens are valid only while a drag from one of this process's native widgets
# is alive. Text supplied by a website or another application is never a tab ID.
_drags = {}
_sidebars = []


class NativeReorder:
    def __init__(self, sidebar):
        self.sidebar = sidebar
        self.dragging = False
        self.diagnostics = []
        _sidebars.append(self)
        self.target(sidebar.favorite_grid, 'tab', self.favorite_destination)

    def trace(self, **event):
        if os.environ.get('VIOLA_QA_DRAG') == '1':
            self.diagnostics.append(event)
            self.diagnostics[:] = self.diagnostics[-40:]

    def close(self):
        if self in _sidebars:
            _sidebars.remove(self)
        for token, payload in list(_drags.items()):
            if payload['owner'] is self:
                del _drags[token]

    def source(self, widget, kind, identity):
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        token = [None]
        def prepare(_source, _x, _y):
            token[0] = uuid.uuid4().hex
            _drags[token[0]] = dict(owner=self, kind=kind, id=identity)
            source.set_icon(Gtk.WidgetPaintable.new(widget), 0, 0)
            return Gdk.ContentProvider.new_for_value(token[0])
        source.connect('prepare', prepare)
        def visibility(active):
            for reorder in list(_sidebars):
                reorder.dragging = active and kind == 'tab'
                reorder.sidebar.render_favorite_drop_zone()
            return False
        source.connect('drag-begin', lambda *_: GLib.idle_add(visibility, True))
        def ended(*_):
            if token[0]:
                _drags.pop(token[0], None)
            GLib.idle_add(visibility, False)
        source.connect('drag-end', ended)
        def cancelled(_source, drag, reason):
            payload = _drags.get(token[0])
            detach = False
            if (payload and kind == 'tab' and reason in (Gdk.DragCancelReason.NO_TARGET, Gdk.DragCancelReason.ERROR)
                    and not self.sidebar.state.get('incognito')):
                surface, x, y = drag.get_device().get_surface_at_position()
                # A rejected drop inside a browser is not a tear-off. Escape
                # has a different cancellation reason and never creates a window.
                inside = any(surface is reorder.sidebar.host.get_surface() and
                             0 <= x < surface.get_width() and 0 <= y < surface.get_height()
                             for reorder in _sidebars)
                detach = not inside
            self.trace(event='cancel', kind=kind, reason=str(reason), detach=detach)
            ended()
            if detach:
                GLib.idle_add(lambda: self.sidebar.host.window_manager.detach_tab(
                    self.sidebar.host, identity) or False)
            return detach
        source.connect('drag-cancel', cancelled)
        widget.add_controller(source)
        return source

    def target(self, widget, kind, destination):
        if hasattr(widget, 'viola_drop_destinations'):
            widget.viola_drop_destinations[kind] = destination
            return widget.viola_drop_target
        widget.viola_drop_destinations = {kind: destination}
        target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
        widget.viola_drop_target = target
        target.set_preload(True)
        def clear(*_):
            widget.remove_css_class('viola-drop-before')
            widget.remove_css_class('viola-drop-after')
        def motion(_target, _x, y):
            payload = _drags.get(target.get_value())
            clear()
            if not payload or payload['kind'] not in widget.viola_drop_destinations:
                return Gdk.DragAction(0)
            widget.add_css_class('viola-drop-before' if y < widget.get_height()/2 else 'viola-drop-after')
            return Gdk.DragAction.MOVE
        target.connect('enter', motion)
        target.connect('motion', motion)
        target.connect('leave', clear)
        def dropped(_target, token, x, y):
            clear()
            payload = _drags.get(token)
            if not payload or payload['kind'] not in widget.viola_drop_destinations:
                return False
            source = payload['owner'].sidebar
            if source.state.get('incognito') or self.sidebar.state.get('incognito'):
                return False
            result = widget.viola_drop_destinations[payload['kind']](payload['id'], x, y)
            if result is None:
                return False
            channel, parameters = result
            self.trace(event='drop', channel=channel, parameters=parameters)
            self.sidebar.host.send(channel, parameters)
            return True
        target.connect('drop', dropped)
        widget.add_controller(target)
        return target

    def favorite_destination(self, identity, x, y):
        state = self.sidebar.state
        ids = [tab['id'] for tab in state.get('favorites', []) if tab['id'] != identity]
        return self.tab_command(identity, state.get('activeSpaceId'), 'favorite', len(ids))

    @staticmethod
    def tab_command(identity, space, section, index):
        if not space:
            return None
        return 'tab:reorder', dict(tabId=identity, spaceId=space, section=section, index=index)

    def tab(self, widget):
        self.source(widget.activate, 'tab', widget.tab_id)
        def destination(identity, x, y):
            state = self.sidebar.state
            section = widget.section
            rows = state.get('favorites' if section == 'favorite' else section, [])
            space = widget.source_space_id or state.get('activeSpaceId')
            ids = [tab['id'] for tab in rows if tab['id'] != identity and (
                section == 'favorite' or not state.get('isAllWorkspaces') or tab.get('sourceSpaceId') == space)]
            if widget.tab_id == identity or widget.tab_id not in ids:
                return None
            after = x >= widget.get_width()/2 if section == 'favorite' else y >= widget.get_height()/2
            if section != 'favorite':
                group = next((group for group in state.get('tabGroups', [])
                              if widget.tab_id in group.get('tabIds', [])), None)
                if group and not group['id'].startswith('split:'):
                    members = [key for key in group['tabIds'] if key != identity]
                    return 'tabGroup:groupTab', dict(tabId=identity, targetTabId=None,
                        groupId=group['id'], spaceId=space,
                        index=members.index(widget.tab_id)+int(after))
            return self.tab_command(identity, space, section, ids.index(widget.tab_id)+int(after))
        self.target(widget, 'tab', destination)

    def workspace(self, widget, identity):
        self.source(widget, 'workspace', identity)
        def destination(source, _x, y):
            ids = [space['id'] for space in self.sidebar.state.get('spaces', []) if space['id'] != source]
            if source == identity or identity not in ids:
                return None
            index = ids.index(identity) + int(y >= widget.get_height()/2)
            return 'space:update', dict(spaceId=source, patch={'index': index})
        self.target(widget, 'workspace', destination)

    def tab_section(self, widget, space):
        def destination(identity, _x, _y):
            state = self.sidebar.state
            rows = [tab for tab in state.get('today', []) if tab['id'] != identity and (
                not state.get('isAllWorkspaces') or tab.get('sourceSpaceId') == space)]
            return self.tab_command(identity, space, 'today', len(rows))
        self.target(widget, 'tab', destination)
