# SPDX-License-Identifier: Apache-2.0
"""Exercise real responsive Phone lists and entry activation, without live calls."""
import os
from pathlib import Path
import unittest
import time
from unittest.mock import patch
from phone_dial_runtime import find
from gi.repository import Gio, GLib

fixture = Path(__file__).resolve().parent / 'fixtures/phone-v70.json'
assert fixture.is_file(), 'The private Phone navigation fixture is required'
os.environ['LUMA_PHONE_FIXTURE'] = str(fixture)
from prairie_apps.phone import PhoneApplication, PhoneWindow


def settle():
    deadline = time.monotonic() + .3
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class NavigationWorkflow(unittest.TestCase):
    def test_lists_and_recent_sidebar_across_widths(self):
        app = PhoneApplication()
        app.set_application_id('org.projectluma.Phone.NavigationTest')
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        try:
            for width in (360, 500, 1024, 1440):
                with self.subTest(width=width):
                    with patch.object(PhoneWindow, '_start_live', side_effect=AssertionError('live call service requested')):
                        window = PhoneWindow(app)
                    try:
                        window.set_default_size(width, 874)
                        window.present(); settle()
                        for tab in ('recents', 'contacts', 'vm'):
                            window._switch(tab); settle()
                            self.assertFalse(window.drill)
                            destination = window.sidebar.list if window.is_phone else find(window, 'pn-main-list')
                            self.assertIsNotNone(destination)
                            prefix = {'recents': 'pn-recent-', 'contacts': 'pn-person-', 'vm': 'pn-vm-'}[tab]
                            row = destination.get_first_child()
                            while row is not None and not row.get_name().startswith(prefix):
                                row = row.get_next_sibling()
                            self.assertIsNotNone(row, f'{tab} has no activatable entry')
                            self.assertTrue(row.get_name().startswith(prefix), row.get_name())
                            if not window.is_phone:
                                call = window.sidebar.list.get_first_child()
                                while call:
                                    self.assertTrue(call.get_name().startswith('pn-recent-'))
                                    call = call.get_next_sibling()
                            row.emit('activate'); settle()
                            self.assertTrue(window.drill)
                            window._back(); settle()
                            self.assertFalse(window.drill)
                            if not window.is_phone:
                                self.assertIsNotNone(find(window, 'pn-main-list'))
                    finally:
                        window.close(); settle()
        finally:
            app.run_dispose()


if __name__ == '__main__':
    unittest.main(verbosity=2)
