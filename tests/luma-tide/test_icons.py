# SPDX-License-Identifier: Apache-2.0
"""Tide's controls use only the kit's vendored Lucide glyphs."""
import ast
import json
import unittest
from pathlib import Path
import luma_tide
from luma_appkit.icons import search_paths

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(luma_tide.__file__).resolve().parent
FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/tide-v70.json'

class IconContractTest(unittest.TestCase):
    def test_every_control_icon_is_vendored(self):
        icons = search_paths()
        self.assertTrue(icons, 'the required installed LumaUI kit must supply its icons')
        names = set()
        modules = list(SOURCE.glob('*.py'))
        self.assertTrue(modules, 'the tested Tide package must contain source modules')
        for path in modules:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                if fn in ('action', 'StackedButton', 'BarAction', 'image') and node.args:
                    if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        names.add(node.args[0].value)
                for keyword in node.keywords:
                    if keyword.arg == 'icon' and isinstance(keyword.value, ast.Constant):
                        names.add(keyword.value.value)
        fixture = json.loads(FIXTURE.read_text())
        names.update(s['icon'] for s in fixture['sources'])
        names.update(row[1] for row in fixture['player']['outputs'])
        self.assertIn('share-2', names)
        for name in names:
            with self.subTest(icon=name):
                if name == 'org.projectluma.Tide':
                    # A shared file-share payload carries the application's
                    # identity, rather than a symbolic control glyph.
                    self.assertTrue((SOURCE.parent / 'data' / f'{name}.svg').is_file())
                else:
                    self.assertTrue(any((folder / f'lumaui-{name}-symbolic.svg').is_file() for folder in icons))
