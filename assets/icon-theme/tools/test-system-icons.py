#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('validator', Path(__file__).with_name('validate-system-icons.py'))
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class ArtworkAdmission(unittest.TestCase):
    def test_local_vector_references(self):
        validator.validate_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><path id="p" d="M0 0h1v1z"/></defs><use href="#p"/></svg>', 'fixture')

    def test_active_external_and_unresolved_resources(self):
        for child in ('<script/>', '<image href="data:image/png;base64,AA=="/>',
                      '<use href="https://example.test/x.svg#p"/>', '<use href="#missing"/>',
                      '<path fill="url(https://example.test/paint)"/>', '<text>Font</text>',
                      '<path onclick="x()"/>', '<filter/>'):
            with self.subTest(child=child), self.assertRaises(ValueError):
                validator.validate_svg(('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">' + child + '</svg>').encode(), 'fixture')

    def test_entity_declaration(self):
        with self.assertRaises(ValueError):
            validator.validate_svg(b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg/>', 'fixture')

    def test_path_escape(self):
        for path in ('/absolute.svg', '../../escape.svg'):
            with self.assertRaises(ValueError):
                validator.relative(path)

    def test_byte_change_and_unapproved_context_file(self):
        original = Path(__file__).resolve().parents[1] / 'Prairie'
        with tempfile.TemporaryDirectory(prefix='luma-system-icon-test-') as directory:
            theme = Path(directory) / 'Prairie'
            shutil.copytree(original, theme)
            args = (theme, theme/'system-manifest.json', theme/'system-integration.json')
            icon = theme / 'scalable/places/folder.svg'
            data = icon.read_bytes()
            icon.write_bytes(data + b'\n')
            with self.assertRaisesRegex(ValueError, 'Approved bytes changed'):
                validator.validate(*args)
            icon.write_bytes(data)
            (theme/'scalable/devices/unapproved.svg').write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'Unapproved devices payload'):
                validator.validate(*args)


if __name__ == '__main__':
    unittest.main()
