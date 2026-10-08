# SPDX-License-Identifier: Apache-2.0
"""Actual GTK repair view/Close when migration cannot provide a ready reply."""
import gi
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw, GLib, Gio
from luma_appkit import AppWindow
from luma_appkit.migration_startup import review_window, repair_application_id, repair_application

class RepairScreen(unittest.TestCase):
    def test_repair_registration_keeps_actual_sandbox_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            info=Path(directory)/'info'
            info.write_text('[Application]\nname=org.projectluma.Notes\n')
            self.assertEqual(repair_application_id(info),'org.projectluma.Notes')
            with patch('luma_appkit.migration_startup.repair_application_id',
                       return_value=repair_application_id(info)):
                app=repair_application()
            self.assertEqual(app.get_application_id(),'org.projectluma.Notes')
            self.assertTrue(app.get_flags() & Gio.ApplicationFlags.NON_UNIQUE)
            info.write_text('[Application]\nname=invalid\n')
            with self.assertRaises(RuntimeError): repair_application_id(info)
            info.unlink()
            self.assertEqual(repair_application_id(info),'org.projectluma.DataMigrationReview')

    def test_unavailable_migration_preserves_frame_and_real_close_action(self):
        app = Adw.Application(application_id='org.projectluma.DataMigrationReviewTest',
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        observed=[];errors=[]
        def activated(application):
            try:
                window,state = review_window(application,
                    'Your existing data is preserved. Close the native app and try again, or review migration in Depot.')
                window.present()
                def check():
                    try:
                        self.assertIsInstance(window,AppWindow)
                        self.assertTrue(window.get_mapped())
                        self.assertIs(window.body.get_first_child(),state)
                        self.assertIsNot(window.get_content(),state)
                        self.assertGreater(state.primary_button.get_width(),0)
                        self.assertTrue(state.primary_button.get_mapped())
                        self.assertEqual(state.heading.get_text(),'Your data is safe')
                        self.assertIn('existing data is preserved',state.description.get_text())
                        observed.append(True)
                        state.primary_button.emit('clicked')
                    except BaseException as error:
                        errors.append(error);application.quit()
                    return GLib.SOURCE_REMOVE
                GLib.timeout_add(250,check)
            except BaseException as error:
                errors.append(error);application.quit()
        app.connect('activate',activated)
        self.assertEqual(app.run([]),0)
        if errors: raise errors[0]
        self.assertEqual(observed,[True])

if __name__=='__main__':unittest.main()
