# SPDX-License-Identifier: GPL-3.0-only
"""Compositor-delivered organization drags against a disposable real profile."""
from gi.repository import GLib
import os
from urllib.parse import urlsplit
from qualify_native_windows import WindowQualification


class DragQualification:
    def __init__(self, controls):
        self.controls = controls
        self.window = controls.window
        self.services = self.window.services
        self.wait = lambda predicate, then: WindowQualification.wait(self, predicate, then)
        self.report = controls.report
        def finish(error=None):
            self.report['drag_events'] = list(self.window.sidebar.reorder.diagnostics)
            self.report['window_count'] = len(self.window.window_manager.contexts)
            self.report['window_states'] = [{
                'id': key, 'active': context['window'].services.state.get('activeTabId'),
                'url': context['window'].services.state.get('activeUrl'),
                'mapped': context['window'].get_mapped()}
                for key, context in self.window.window_manager.contexts.items()]
            self.report['favorite_ids'] = [row['id'] for row in self.services.state.get('favorites', [])]
            controls.finish(error)
        self.finish = finish
        self.window.submit(self.seed, self.seeded)

    def seed(self):
        services = self.services
        parts = urlsplit(services.state['activeUrl'])
        origin = parts.scheme + '://' + parts.netloc
        self.space = services.state['activeSpaceId']
        self.tabs = []
        for name in ('Drag-A', 'Drag-B', 'Drag-C'):
            url = origin + '/layout?title=' + name
            services.navigate(url, new_tab=True)
            state = services.wait_state(lambda state: state.get('activeUrl') == url)
            self.tabs.append(state['activeTabId'])

    def seeded(self, _):
        self.wait(lambda: all(key in self.window.rows for key in self.tabs), self.reorder_tabs)

    def check(self, name):
        self.report['checks'].append({name: True})

    def position(self, widget, fraction=(.5, .5)):
        valid, bounds = widget.compute_bounds(self.window)
        if not valid:
            raise RuntimeError('Drag widget has no native window bounds')
        return ((1600-self.window.get_width())/2 + bounds.get_x()+bounds.get_width()*fraction[0],
                (1000-self.window.get_height())/2 + bounds.get_y()+bounds.get_height()*fraction[1])

    def drag(self, source, destination, fraction, then):
        device = self.controls.device
        start = self.position(source)
        device.move(*start)
        def button(pressed):
            device.call(device.session, device.service + '.Session', 'NotifyPointerButton',
                        GLib.Variant('(ib)', (272, pressed)))
        def relative(dx, dy):
            device.call(device.session, device.service + '.Session', 'NotifyPointerMotionRelative',
                        GLib.Variant('(dd)', (float(dx), float(dy))))
        def begin():
            button(True)
            GLib.timeout_add(100, lambda: relative(12, 0) or False)
            GLib.timeout_add(350, move_to_target)
            return False
        def move_to_target():
            target = destination()
            end = target if isinstance(target, tuple) else self.position(target, fraction)
            relative(end[0]-start[0]-12, end[1]-start[1])
            GLib.timeout_add(300, drop)
            return False
        def drop():
            button(False)
            GLib.timeout_add(200, lambda: then() or False)
            return False
        GLib.timeout_add(80, begin)

    def reorder_tabs(self):
        self.drag(self.window.rows[self.tabs[2]].activate,
                  lambda: self.window.rows[self.tabs[0]], (.5, .2), self.tabs_dropped)

    def tabs_dropped(self):
        def ordered():
            ids = [row['id'] for row in self.services.state['today']]
            return ids.index(self.tabs[2]) < ids.index(self.tabs[0])
        self.wait(ordered, self.favorite_drag)

    def favorite_drag(self):
        self.check('physical_tab_reorder')
        self.drag(self.window.rows[self.tabs[0]].activate,
                  lambda: self.window.sidebar.favorite_grid, (.5, .5), self.favorite_dropped)

    def favorite_dropped(self):
        self.wait(lambda: any(row['id'] == self.tabs[0] for row in self.services.state['favorites']),
                  self.create_workspace)

    def create_workspace(self):
        self.check('physical_drag_into_empty_favorites')
        def create():
            self.services.send('sidebar', 'space:create', {'name': 'Drag workspace', 'icon': '◫'})
            return self.services.wait_state(lambda state: len(state['spaces']) >= 2)
        self.window.submit(create, self.workspace_created)

    def workspace_created(self, state):
        self.second_space = next(space['id'] for space in state['spaces'] if space['id'] != self.space)
        self.window.send('space:switch', {'spaceId': self.second_space})
        self.wait(lambda: self.services.state.get('activeSpaceId') == self.second_space,
                  self.favorite_global)

    def favorite_global(self):
        if not any(row['id'] == self.tabs[0] for row in self.services.state['favorites']):
            self.finish('Favorites disappeared when switching workspace')
            return
        self.check('favorites_survive_workspace_switch')
        if os.environ.get('VIOLA_QA_DRAG_TAB_ONLY') == '1':
            self.finish()
            return
        scope = next(scope['id'] for scope in self.services.state['workspaceScopes'] if scope.get('isAll'))
        self.window.send('space:switch', {'spaceId': scope})
        self.wait(lambda: self.window.sidebar.sections.get(self.second_space) is not None and
                  self.window.sidebar.sections[self.second_space].get_mapped(), self.reorder_workspace)

    def reorder_workspace(self):
        self.drag(self.window.sidebar.sections[self.second_space].header,
                  lambda: self.window.sidebar.sections[self.space].header, (.5, .2), self.workspace_dropped)

    def workspace_dropped(self):
        self.wait(lambda: self.services.state['spaces'][0]['id'] == self.second_space, self.finished)

    def finished(self):
        self.check('physical_workspace_reorder')
        self.drag(self.window.rows[self.tabs[1]].activate, lambda: self.window.page, (.5, .5), self.inside_rejected)

    def inside_rejected(self):
        if len(self.window.window_manager.contexts) != 1:
            self.finish('Dropping inside the page unexpectedly detached the tab')
            return
        self.check('inside_page_drop_does_not_detach')
        self.drag(self.window.rows[self.tabs[1]].activate, lambda: (20, 20), (.5, .5), self.detached)

    def detached(self):
        manager = self.window.window_manager
        self.wait(lambda: len(manager.contexts) == 2 and any(
            c['window'] is not self.window and c['window'].services.state.get('activeTabId') == self.tabs[1]
            and c['window'].get_mapped() and c['window'].page_input.valid_identity()
            for c in manager.contexts.values()), self.detach_ready)

    def detach_ready(self):
        self.check('physical_tab_drop_outside_opens_focused_window')
        self.finish()
