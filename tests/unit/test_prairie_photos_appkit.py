# SPDX-License-Identifier: Apache-2.0
"""Architecture contracts; visual conformance is measured by lumaui-conform."""
import ast
import json
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'src/prairie-core/prairie_apps/photos.py'


class PhotosAppKitContractTests(unittest.TestCase):
    def test_window_composes_shared_parts_and_never_delegates_editing(self):
        tree=ast.parse(SOURCE.read_text())
        window=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='PhotosWindow')
        self.assertEqual([ast.unparse(base) for base in window.bases],['AppWindow'])
        calls={ast.unparse(node.func) for node in ast.walk(window) if isinstance(node,ast.Call)}
        required={'Island','NavigationSidebar','SidebarToggle','ActionCenter','ActionEditor','CornerPill','DetailsPane','ToastHost'}
        self.assertFalse(required-calls,f'Missing semantic kit composition: {required-calls}')
        self.assertFalse({'subprocess.run','subprocess.Popen','os.system'} & calls)
        self.assertNotIn('prairie_ui',SOURCE.read_text())
        assigned={ast.unparse(target) for node in ast.walk(window) if isinstance(node,ast.Assign) for target in node.targets}
        self.assertNotIn('self.body',assigned,'AppWindow owns body; replacing it creates a cyclic widget tree in set_body')

    def test_app_css_uses_tokens_without_restyling_kit(self):
        css=(ROOT/'src/prairie-core/style/photos.css').read_text()
        css=re.sub(r'/\*.*?\*/','',css,flags=re.S)
        for selector,body in re.findall(r'([^{}]+)\{([^{}]*)\}',css):
            self.assertTrue(all(item.strip().startswith('.ph-') for item in selector.split(',')),selector)
            for declaration in body.split(';'):
                if not declaration.strip():continue
                name,value=declaration.split(':',1)
                if name.strip() in ('color','background','border-radius','font-size','font-weight','box-shadow'):
                    self.assertRegex(value,r'@luma_|var\(--lumaui-',declaration)
        self.assertNotRegex(css,r'#[0-9a-fA-F]{3,8}\b|rgba?\(|\d+(?:\.\d+)?px|\.lumaui-|@define-color')

    def test_fixture_and_scenario_are_safe_and_complete(self):
        scenario=json.loads((ROOT/'tools/lumaui-conform/scenarios/photos.json').read_text())
        self.assertEqual(scenario['gtk']['env']['LUMA_PHOTOS_FIXTURE'],'{run}/fixtures/photos-v70.json')
        self.assertIn('tests/fixtures/photos-v70',scenario['gtk']['fixtures'])
        required={'library','fav','people','places','launch','walls','deleted','years','months','all','viewer','information','more','edit-light','edit-looks','edit-colour','edit-crop','edit-auto','import','new-album'}
        states={item['name'] for item in scenario['states']}
        self.assertFalse(required-states,f'Missing v70 states: {required-states}')
        self.assertTrue(states.issubset(set(scenario['phone_states'])))
        fixture=json.loads((ROOT/'tests/fixtures/photos-v70.json').read_text())
        for photo in fixture['photos']:
            path=Path(photo['image'])
            self.assertFalse(path.is_absolute())
            self.assertNotIn('..',path.parts)
            self.assertEqual(path.parts[0],'photos-v70')

    def test_camera_and_filer_open_indexed_asset_through_application(self):
        tree=ast.parse(SOURCE.read_text())
        methods={node.name:node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef)}
        self.assertIn('do_open',methods)
        self.assertIn('request_open_path',methods)
        calls={ast.unparse(node.func) for node in ast.walk(methods['do_open']) if isinstance(node,ast.Call)}
        self.assertIn('self.props.active_window.request_open_path',calls)
        self.assertIn('Gio.ApplicationFlags.HANDLES_OPEN',SOURCE.read_text())
        desktop=(ROOT/'src/prairie-core/data/org.projectluma.Photos.desktop').read_text()
        self.assertIn('Exec=prairie-photos %U',desktop)


if __name__=='__main__':unittest.main()
