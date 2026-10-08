# SPDX-License-Identifier: Apache-2.0
"""Drive the packaged This machine view under Xvfb, against a fixture report.

The fixture carries one of every result, including a capability checked at two
tiers, so the view is exercised on the states that matter: a broken row that
shows its remedy, an unproven row that says why, and an absent row that is
quiet.
"""
import json
import os
import tempfile
import time
import traceback
from pathlib import Path

directory = Path(tempfile.mkdtemp())
fixture = directory / 'capability-report.json'
checker = directory / 'luma-capability-check'
checker.write_text('#!/bin/sh\nexit 0\n')
checker.chmod(0o755)
fixture.write_text(json.dumps({
    'version': 1,
    'written_at': time.time() - 3 * 3600,
    'hostname': 'fixture',
    'capabilities': [
        {'id': 'video-decode-hardware', 'title': 'Hardware video decode',
         'promise': 'Video plays without the processor decoding it.',
         'remedy': 'The graphics driver for video is installed but nothing is using it; '
                   'video is being decoded on the processor.',
         'severity': 'required', 'tier': 'hardware', 'result': 'fail',
         'detail': 'the video engine counter did not move'},
        {'id': 'vulkan', 'title': 'Vulkan', 'promise': 'Applications that need Vulkan find it.',
         'remedy': 'Vulkan is not available.', 'severity': 'expected', 'tier': 'hardware',
         'result': 'unproven', 'detail': 'no render node in this session'},
        {'id': 'wifi', 'title': 'Wi-Fi', 'promise': 'Wireless networks work.',
         'remedy': 'Wireless is not working.', 'severity': 'expected', 'tier': 'session',
         'result': 'pass', 'detail': 'one wireless device'},
        {'id': 'wifi', 'title': 'Wi-Fi', 'promise': 'Wireless networks work.',
         'remedy': 'Wireless is not working.', 'severity': 'expected', 'tier': 'hardware',
         'result': 'pass', 'detail': 'associated'},
        {'id': 'camera', 'title': 'Camera', 'promise': 'The camera works in applications.',
         'remedy': 'The camera is not working.', 'severity': 'expected', 'tier': 'hardware',
         'result': 'not-applicable', 'detail': 'this machine has no camera'},
        {'id': 'printing-discovery', 'title': 'Finding printers',
         'promise': 'Printers on the network are found.', 'remedy': 'Printers are not found.',
         'severity': 'expected', 'tier': 'session', 'result': 'skip', 'detail': ''},
    ],
}))
os.environ['LUMA_CAPABILITY_REPORT'] = str(fixture)
os.environ['LUMA_CAPABILITY_CHECKER'] = str(checker)

from luma_monitor.machine_application import MACHINE, MonitorApplication, GLib  # noqa: E402
from luma_monitor import capability  # noqa: E402

application = MonitorApplication()
failures, checks = [], []


def labels(widget, found=None):
    found = [] if found is None else found
    child = widget.get_first_child()
    while child is not None:
        if hasattr(child, 'get_label') and child.get_label():
            found.append(child.get_label())
        labels(child, found)
        child = child.get_next_sibling()
    return found


def verify():
    try:
        window = application.get_active_window()
        assert window is not None
        window.preferences = Path(tempfile.mkdtemp()) / 'preferences.json'

        assert capability.REPORT == str(fixture), capability.REPORT
        window.show_machine()
        assert window.tab == MACHINE
        assert window.table_stack.get_visible_child_name() == MACHINE
        assert window.machine.get_visible_child_name() == 'list'

        # Six entries, five capabilities: Wi-Fi is checked at two tiers.
        rows = []
        child = window.machine.rows.get_first_child()
        while child is not None:
            rows.append(labels(child))
            child = child.get_next_sibling()
        assert len(rows) == 5, rows

        text = ['\n'.join(row) for row in rows]
        assert 'Hardware video decode' in text[0] and 'Not working' in text[0], text[0]
        assert 'nothing is using it' in text[0], text[0]
        assert 'Video plays without the processor decoding it.' in text[0], text[0]
        assert 'Not checked here' in text[1] and 'no render node in this session' in text[1], text[1]
        assert 'Working' in text[2] and 'Not working' not in text[2], text[2]
        # A capability this machine does not have is quiet: no remedy, and not
        # a failure.
        assert 'Not on this machine' in text[3], text[3]
        assert 'The camera is not working.' not in text[3], text[3]
        assert 'Not checked here' in text[4], text[4]

        # The process chrome is out of the way, and the way back is in place.
        assert not window.search.get_visible()
        assert not window.footer.get_visible()
        assert window.recheck.get_visible()
        assert window.status_left.get_text() == 'fixture · checked 3 hours ago', \
            window.status_left.get_text()

        # And the machine that has never been checked: the empty state offers
        # the check itself, so the toolbar does not offer it a second time.
        capability.REPORT = str(directory / 'absent.json')
        window.refresh_machine()
        assert window.machine.get_visible_child_name() == 'unchecked'
        assert not window.recheck.get_visible()
        assert window.status_left.get_text() == capability.NOT_CHECKED_YET
        capability.REPORT = str(fixture)
        window.refresh_machine()
        assert window.machine.get_visible_child_name() == 'list'
        assert window.recheck.get_visible()

        from luma_appkit import icons
        from gi.repository import Gtk
        for group in window.commands.visible_groups(menu=True):
            for command in group.commands:
                assert command.icon, command.id
                assert Gtk.IconTheme.get_for_display(window.get_display()).has_icon(icons.resolve(command.icon)), command.icon

        window.buttons['cpu'].set_active(True)
        assert window.search.get_visible() and window.footer.get_visible()
        assert window.table_stack.get_visible_child_name() == 'table'
        window.close()
        checks.append('machine view: states, remedy, one row per capability, empty state')
    except Exception:
        failures.append(traceback.format_exc())
    application.quit()
    return GLib.SOURCE_REMOVE


GLib.timeout_add(2500, verify)
application.run([])
if failures:
    raise AssertionError('\n'.join(failures))
assert checks
print('Monitor machine runtime passed:', checks[0])
