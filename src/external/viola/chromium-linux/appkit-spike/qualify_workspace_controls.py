# SPDX-License-Identifier: GPL-3.0-only
"""Additional native layout and existing workspace-command qualification."""
from gi.repository import GLib, Gtk
from luma_appkit.menus import _descendants


class WorkspaceQualification:
    def __init__(self, controls):
        self.controls = controls
        self.window = controls.window
        self.layout = self.window.sidebar_layout
        self.resize_distances = iter((80, -120, 40))
        self.resize_round = 0
        self.drag(64, self.resized)

    def drag(self, distance, then):
        device = self.controls.device
        self.controls.point(self.layout.handle, (.5, .5), click=False)
        def button(pressed):
            device.call(device.session, device.service + '.Session', 'NotifyPointerButton',
                        GLib.Variant('(ib)', (272, pressed)))
        def motion():
            device.call(device.session, device.service + '.Session', 'NotifyPointerMotionRelative',
                        GLib.Variant('(dd)', (float(distance), 0.0)))
        GLib.timeout_add(80, lambda: button(True) or False)
        GLib.timeout_add(160, lambda: motion() or False)
        GLib.timeout_add(240, lambda: button(False) or False)
        GLib.timeout_add(420, then)

    def check(self, label, passed):
        self.controls.report['checks'].append({label: bool(passed)})
        if not passed:
            self.controls.finish(label)
        return bool(passed)

    def resized(self):
        if not self.check('sidebar_resize_316', self.window.sidebar.get_width() == 316):
            return False
        GLib.timeout_add(700, self.resize_frame_settled)
        return False

    def resize_frame_settled(self):
        from gpu_page import buffer_fits_viewport
        page = self.window.page
        frame = page.frame
        diagnostic = dict(
            expected=page.expected_buffer_size,
            received=(frame['width'], frame['height']) if frame else None,
            received_id=frame['id'] if frame else None,
            displayed=page.displayed_frame['id'] if page.displayed_frame else None)
        self.controls.report.setdefault('sidebar_resize_frames', []).append(diagnostic)
        self.resize_round += 1
        if not self.check(f'sidebar_release_receives_matching_frame_{self.resize_round}', frame is not None
                          and page.texture is not None and page.displayed_frame is not None
                          and page.displayed_frame['id'] == frame['id']
                          and buffer_fits_viewport((frame['width'], frame['height']),
                                                   page.expected_buffer_size)):
            return False
        distance = next(self.resize_distances, None)
        if distance is not None:
            self.drag(distance, lambda: GLib.timeout_add(700, self.resize_frame_settled) and False)
        else:
            self.drag(-220, self.collapsed)
        return False

    def collapsed(self):
        if not self.check('sidebar_drag_below_threshold_collapses', self.layout.collapsed):
            return False
        self.controls.point(self.layout.edge, (.5, .5), click=False)
        GLib.timeout_add(180, self.revealed)
        return False

    def revealed(self):
        if not self.check('sidebar_hover_reuses_widget_and_width', self.layout.peeking
                          and self.window.sidebar.get_mapped() and self.window.sidebar.get_width() == 316):
            return False
        self.controls.point(self.window.page, (.8, .8), click=False)
        GLib.timeout_add(350, self.hidden)
        return False

    def hidden(self):
        if not self.check('sidebar_unhover_hides', not self.layout.peeking and not self.window.sidebar.get_visible()):
            return False
        self.layout.toggle()
        self.window.sidebar.open_workspace_picker()
        GLib.timeout_add(200, self.picker)
        return False

    def picker(self):
        menu = self.window.open_popover
        dots = [w for w in _descendants(menu) if w.has_css_class('viola-workspace-dot')]
        if not self.check('workspace_picker_window_owned_with_colors', menu.get_parent() is self.window and len(dots) > 0):
            return False
        self.controls.capture_stage('workspaces', self.open_editor)
        return False

    def open_editor(self):
        menu = self.window.open_popover
        command = next(c for c, label, _ in menu.custom_rows.values() if label == 'Edit workspaces…')
        command.execute()
        GLib.timeout_add(200, self.editor)
        return False

    def editor(self):
        editor = self.window.sidebar.settings_dialog
        if not self.check('edit_workspaces_native_dialog', editor is not None and len(editor.rows) > 0):
            return False
        self.controls.capture_stage('workspace-editor', self.edit_name)
        return False

    def edit_name(self):
        editor = self.window.sidebar.settings_dialog
        self.editor_widget = editor
        self.key, entry = next(iter(editor.rows.items()))
        self.original = entry.get_text()
        entry.set_text('Native qualification workspace')
        editor.rename(self.key, entry)
        GLib.timeout_add(350, self.renamed)
        return False

    def renamed(self):
        space = next(s for s in self.window.state['spaces'] if s['id'] == self.key)
        if not self.check('workspace_editor_renames_original_store', space['name'] == 'Native qualification workspace'):
            return False
        entry = self.editor_widget.rows[self.key]
        entry.set_text(self.original)
        self.editor_widget.rename(self.key, entry)
        new_entry = Gtk.Entry(text='Workspace menu qualification')
        self.editor_widget.create(new_entry)
        GLib.timeout_add(350, self.created)
        return False

    def created(self):
        space = next((s for s in self.window.state['spaces'] if s['name'] == 'Workspace menu qualification'), None)
        if not self.check('workspace_editor_creates_original_store', space is not None):
            return False
        self.created_key = space['id']
        self.editor_widget.close()
        self.window.send('space:switch', {'spaceId': self.key})
        self.layout.saved_width = 252
        self.layout.width(252)
        self.window.open_retained_menu(self.window.sidebar.switcher, 'tabs:menu',
            {'spaceId': self.key, 'workspaceHeader': True})
        GLib.timeout_add(300, self.context)
        return False

    def context(self):
        menu = self.window.open_popover
        rows = [dict(label=label, path=list(path)) for path, name in menu.paths.items()
                for command, label, theme in menu.custom_rows.values() if command.id == name]
        self.controls.report['workspace_context_rows'] = rows
        self.controls.report['workspace_context_widgets'] = [dict(type=type(w).__name__, mapped=w.get_mapped(),
            height=w.get_height(), label=w.get_text() if isinstance(w, Gtk.Label) else '')
            for w in _descendants(menu) if isinstance(w, (Gtk.Label, Gtk.Separator))]
        scroller = next(w for w in _descendants(menu) if isinstance(w, Gtk.ScrolledWindow))
        adjustment = scroller.get_vadjustment()
        self.controls.report['workspace_scroll_extent'] = [adjustment.get_upper(), adjustment.get_page_size()]
        if not self.check('workspace_context_all_rows_fit', adjustment.get_upper() <= adjustment.get_page_size() + 1):
            return False
        has_delete = any(row['label'] == 'Delete workspace…' for row in rows)
        if not self.check('workspace_context_has_visible_delete_after_separator', not has_delete or any(
                isinstance(w, Gtk.Label) and w.get_text() == 'Delete workspace…' and w.get_mapped()
                for w in _descendants(menu))):
            return False
        self.controls.capture_stage('workspace-context', self.open_colors)
        return False

    def open_colors(self):
        menu = self.window.open_popover
        label = next(w for w in _descendants(menu) if isinstance(w, Gtk.Label)
                     and w.get_text() == 'Workspace color' and w.get_mapped())
        self.controls.point(label.get_parent(), (.5, .5))
        GLib.timeout_add(250, self.colors_opened)
        return False

    def colors_opened(self):
        menu = self.window.open_popover
        dots = [w for w in _descendants(menu) if w.get_mapped() and w.has_css_class('viola-workspace-dot')]
        if not self.check('workspace_color_submenu_ten_dots', len(dots) == 10):
            return False
        self.submenu = next(w for w in _descendants(menu) if isinstance(w, Gtk.PopoverMenu) and w.get_mapped())
        heading = next(w for w in _descendants(self.submenu) if w.has_css_class('luma-menu-submenu-heading'))
        self.controls.point(heading, (.5, .5), click=False)
        GLib.timeout_add(180, self.colors_pointer)
        return False

    def colors_pointer(self):
        if not self.check('submenu_pointer_mode', self.submenu.has_css_class('pointer-navigation')):
            return False
        self.controls.capture_stage('workspace-colors', self.choose_color)
        return False

    def choose_color(self):
        menu = self.window.open_popover
        command = next(c for c, label, theme in menu.custom_rows.values() if theme == 'mint')
        button = next(w for w in _descendants(self.submenu) if isinstance(w, Gtk.Button)
                      and w.get_action_name() == 'menu.' + command.id)
        self.controls.point(button, (.5, .5))
        GLib.timeout_add(300, self.color_changed)
        return False

    def color_changed(self):
        space = next(s for s in self.window.state['spaces'] if s['id'] == self.key)
        if not self.check('workspace_color_original_command', space.get('theme') == 'mint'):
            return False
        from workspace_editor import confirm_delete
        dialog = confirm_delete(self.window, self.created_key)
        dialog.emit('response', 'delete')
        dialog.close()
        GLib.timeout_add(300, self.deleted)
        return False

    def deleted(self):
        if not self.check('workspace_native_delete_original_store', not any(
                s['id'] == self.created_key for s in self.window.state['spaces'])):
            return False
        GLib.timeout_add(300, self.controls.page_input)
        return False
