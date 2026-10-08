"""Computer-name suggestions read synthetic host state without changing it."""
from pathlib import Path
import tempfile
import unittest

from luma_continuity.cloud_sync import device_name


class ComputerName(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.info = self.root / 'machine-info'
        self.dmi = self.root / 'dmi'
        self.dmi.mkdir()

    def name(self, hostname='luma'):
        return device_name(machine_info=self.info, dmi=self.dmi, hostname=hostname)

    def firmware(self, field, text):
        (self.dmi / field).write_text(text)

    def test_explicit_pretty_hostname_precedes_firmware_and_static(self):
        self.info.write_text('PRETTY_HOSTNAME="Nick’s Spectre"\n')
        self.firmware('product_version', 'Type1ProductConfigId')
        self.assertEqual(self.name('nicks-spectre'), 'Nick’s Spectre')

    def test_explicit_static_name_is_kept(self):
        self.firmware('product_version', 'ThinkPad T14s')
        self.assertEqual(self.name('my-work-computer'), 'my-work-computer')

    def test_firmware_placeholders_fall_back_to_actual_model(self):
        self.firmware('product_name', 'HP Spectre x360')
        for placeholder in ('Type1ProductConfigId', 'To Be Filled By O.E.M.',
                            'Default string', 'System Product Name', 'None', 'Not Specified'):
            with self.subTest(placeholder=placeholder):
                self.firmware('product_version', placeholder)
                self.assertEqual(self.name(), 'HP Spectre x360')

    def test_valid_product_version_remains_a_useful_suggestion(self):
        self.firmware('product_version', 'ThinkPad T14s Gen 3')
        self.assertEqual(self.name('fedora'), 'ThinkPad T14s Gen 3')

    def test_missing_or_placeholder_only_firmware_uses_friendly_fallback(self):
        self.assertEqual(self.name(), 'This computer')
        self.firmware('product_version', 'Type1ProductConfigId')
        self.firmware('product_name', 'System Product Name')
        self.firmware('product_family', 'Default String')
        self.assertEqual(self.name('localhost-live'), 'This computer')

    def test_pretty_hostname_is_data_and_never_a_shell_command(self):
        self.info.write_text('PRETTY_HOSTNAME="Work $(touch never) `literal`"\n')
        self.assertEqual(self.name(), 'Work $(touch never) `literal`')
        self.assertFalse((self.root / 'never').exists())

    def test_malformed_host_state_falls_back_without_changing_it(self):
        original = 'PRETTY_HOSTNAME="unterminated\n'
        self.info.write_text(original)
        self.assertEqual(self.name('my-computer'), 'my-computer')
        self.assertEqual(self.info.read_text(), original)

    def test_suggestion_is_bounded_and_ignores_control_characters(self):
        self.firmware('product_version', 'ThinkPad\x00\n' + 'A' * 80)
        value = self.name()
        self.assertEqual(len(value), 60)
        self.assertTrue(value.isprintable())


if __name__ == '__main__':
    unittest.main()
