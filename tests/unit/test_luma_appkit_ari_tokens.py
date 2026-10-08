"""The approved Ari fragment must activate every app-owned surface and role."""
import ast
import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('token_generator', ROOT / 'scripts/developer/generate-luma-platform-tokens.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class AriTokens(unittest.TestCase):
    def test_app_styles_have_all_metrics_and_colours(self):
        document = generator.load_document()
        namespace = {}
        exec(generator.render_lumaui_python(document), namespace)
        self.assertIn('ARI', namespace)
        # Execute the actual app style builder: all nested metric lookups must resolve.
        import sys
        from types import ModuleType, SimpleNamespace
        from unittest.mock import patch
        kit = ModuleType('luma_appkit')
        kit.lumaui_tokens = SimpleNamespace(**namespace)
        style = ROOT / 'src/luma-ari/ari_ui/lumaui_style.py'
        with patch.dict(sys.modules, {'luma_appkit': kit}):
            module = {}
            exec(compile(style.read_text(), str(style), 'exec'), module)
            css = module['build'](None)
        colours = set(re.findall(r'@luma_(ari_\w+)', css))
        for family in ('dark', 'light'):
            self.assertLessEqual(colours, document['lumaui']['colors'][family].keys())
        self.assertIn('.ari-receipt-surface', css)

    def test_every_app_type_role_is_generated(self):
        document = generator.load_document()
        source = (ROOT / 'src/luma-ari/ari_ui/lumaui_app.py').read_text()
        roles = [node.args[1].value for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id == "ari_text" and len(node.args) > 1
                 and isinstance(node.args[1], ast.Constant)]
        for role in roles:
            self.assertIn('ari_' + role.replace('-', '_'), document['lumaui']['type_scale'])
        self.assertIn('luma_ari_ask', generator.render_lumaui_colors('dark', document))
        self.assertIn('lumaui-ari-reply-max-width', generator.render_lumaui_metrics(document))


if __name__ == '__main__':
    unittest.main()
