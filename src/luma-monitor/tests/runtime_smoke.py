# SPDX-License-Identifier: Apache-2.0
"""Exercise the packaged GTK application against real procfs under Xvfb."""
import tempfile
import traceback
from pathlib import Path
from luma_monitor.machine_application import MonitorApplication, GLib, rate_size, duration

application = MonitorApplication()
failures = []
checks = []

def verify():
    try:
        window = application.get_active_window()
        assert window is not None
        assert rate_size(0) == '0 KB/s'
        assert rate_size(1024 ** 2) == '1.0 MB/s'
        assert duration(2 * 86400 + 4 * 3600) == '2 d 4 h'
        window.preferences = Path(tempfile.mkdtemp()) / 'preferences.json'
        assert window.snapshot is not None, 'No procfs snapshot received'
        assert 'Couldn’t' not in window.status_left.get_text()
        for tab in ('memory', 'disk', 'network', 'energy', 'cpu'):
            window.buttons[tab].set_active(True)
            window.refresh_rows(); window.refresh_footer()
        window.toggle_advanced()
        window.buttons['filesystems'].set_active(True)
        window.refresh_rows()
        window.buttons['cpu'].set_active(True)
        window.search.set_text('no-match-fixture-quick-check')
        window.refresh_rows()
        assert window.table_stack.get_visible_child_name() == 'empty'
        window.search.set_text('')
        window.refresh_rows()
        assert window.table_stack.get_visible_child_name() == 'table'
        window.close()
        assert window.preferences.exists()
        checks.append('real-procfs tabs advanced search preferences')
    except Exception:
        failures.append(traceback.format_exc())
    application.quit()
    return GLib.SOURCE_REMOVE

GLib.timeout_add(2500, verify)
application.run([])
if failures:
    raise AssertionError('\n'.join(failures))
assert checks
print('Monitor runtime smoke passed:', checks[0])
