# SPDX-License-Identifier: Apache-2.0
"""Native LumaUI flow check. Run under Xvfb with the shared platform installed."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

# The test must never change the developer's desktop preferences.
os.environ['GSETTINGS_BACKEND'] = 'memory'

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk
from luma_tester import gui
from luma_appkit.widgets import AppWindow


def main():
    with tempfile.TemporaryDirectory(prefix='luma-tester-ui-') as temporary:
        root = Path(temporary)
        output = Path(os.environ.get('DEV_ARTIFACT_DIR', temporary))
        (root / 'catalog').mkdir()
        os.environ['LUMA_TESTER_CATALOG'] = str(root / 'catalog')
        phase = 0
        failures = []
        settings = Gio.Settings.new('org.gnome.desktop.interface')

        def screenshot(app, name):
            if shutil.which('import') and shutil.which('xdotool'):
                xid = subprocess.check_output(['xdotool', 'search', '--name', '^Luma Tester$'], text=True).splitlines()[-1]
                subprocess.run(['import', '-window', xid, str(output / name)], check=True)

        def inspect():
            nonlocal phase
            app = Gtk.Application.get_default()
            if not app or not app.get_active_window():
                return True
            try:
                assert isinstance(app.window, AppWindow)
                if phase == 0:
                    assert app.stack.get_visible_child_name() == 'welcome'
                    screenshot(app, 'welcome.png')
                    app.commands.invoke('tester.try')
                    assert app.target == 'mobile'
                    assert app.stack.get_visible_child_name() == 'session'
                    assert not app.developer_heading.get_visible()
                    phase = 1
                elif phase == 1:
                    screenshot(app, 'mobile-desktop.png')
                    app.start()
                    assert 'Add a Luma image' in app.status_label.get_label()
                    app.commands.invoke('tester.develop')
                    assert app.developer_heading.get_visible()
                    app.select_target('desktop')
                    assert app.workspace_title.get_label() == 'Luma Desktop'
                    assert app.sidebar.list.get_selected_row() is app.device_rows['desktop']
                    assert app.device_rows['tv'].get_sensitive()
                    app.commands.invoke('tester.advanced')
                    assert app.sheet_handle.card.get_visible()
                    app.sheet_handle.close()
                    # Force real narrow allocation without changing the kit's opening policy.
                    app.window.set_size_request(480, 480)
                    app.window.set_default_size(480, 560)
                    settings.set_string('color-scheme', 'prefer-dark')
                    phase = 2
                elif phase == 2:
                    screenshot(app, 'narrow-dark.png')
                    assert app.run_button.get_allocated_width() > 0
                    assert app.layout.get_collapsed()
                    app.sidebar_toggle.grab_focus()
                    assert app.window.get_focus() is not None
                    app.quit()
                    return False
            except BaseException as exc:
                failures.append(exc)
                app.quit()
                return False
            return True

        GLib.timeout_add(1000, inspect)
        result = gui.launch(str(root / 'state'))
        if failures:
            raise failures[0]
        assert phase == 2
        assert result == 0
        print('LumaUI frame, Try/Develop flow, target selection, developer disclosure, keyboard focus, Advanced and narrow/dark rendering verified.')


if __name__ == '__main__':
    main()
