"""Source/composition guardrails; native interpreter smoke tests behavior."""
from pathlib import Path
import hashlib
import re
import unittest
import subprocess
import importlib.util
import itertools
import json

ROOT = Path(__file__).resolve().parents[2]

class BootContracts(unittest.TestCase):
    def test_canonical_artwork(self):
        self.assertEqual(hashlib.sha256((ROOT/'website/public/brand/luma-wordmark.svg').read_bytes()).hexdigest(),
                         '0ddcb7eda2bccc59a968491163e46fdf125028e90fd0e2fc7d1c85d3e2cc8df7')

    def test_shared_release_composition(self):
        inputs = (ROOT/'config/desktop/inputs.env').read_text()
        nevra = re.search(r'^LUMA_BOOT_THEME_NEVRA=(.+)$', inputs, re.M)[1]
        self.assertIn(nevra, (ROOT/'config/desktop/packages.txt').read_text().splitlines())
        for script in ('scripts/vm/compose-desktop-image.sh', 'scripts/mobile/compose-fp6-rootfs.sh'):
            self.assertIn('$LUMA_BOOT_THEME_NEVRA.rpm', (ROOT/script).read_text())

    def test_real_callback_vocabulary_and_no_simulated_claims(self):
        for name in ('luma-loading','luma-loading-handheld'):
            script = (ROOT/f'assets/boot/{name}.script').read_text()
            for callback in ('DisplayNormal','DisplayPassword','DisplayQuestion','DisplayMessage','HideMessage'):
                self.assertIn(f'Plymouth.Set{callback}Function', script)
            for claim in ('Secure Boot verified','Checking your drives','Getting your files ready','Welcome back'):
                self.assertNotIn(claim, script)
            self.assertNotIn('SetKeyboardInputFunction', script)  # Keep native Escape and entry handling.
            self.assertIn('SetBootProgressFunction', script)  # Only reported native progress.
            self.assertIn('Font=Figtree Medium', (ROOT/f'assets/boot/{name}.plymouth').read_text())

    def test_ink_grub_background(self):
        theme = (ROOT/'assets/boot/grub-theme/theme.txt').read_text()
        self.assertIn('desktop-color: "#21252b"', theme)
        self.assertIn('message-bg-color: "#21252b"', theme)

    def test_all_build_profiles_compile_and_reject_unknown_values(self):
        module = importlib.util.spec_from_file_location('compiler',ROOT/'scripts/boot/compile-theme.py')
        compiler = importlib.util.module_from_spec(module); module.loader.exec_module(compiler)
        template = (ROOT/'assets/boot/luma-theme.script.in').read_text()
        for values in itertools.product(*compiler.CHOICES.values()):
            profile = dict(zip(compiler.CHOICES,values))
            self.assertIn('Plymouth.SetBootProgressFunction',compiler.script(profile,template))
        for key in compiler.CHOICES:
            bad=compiler.MOBILE.copy();bad[key]='unsupported'
            with self.assertRaises(ValueError): compiler.validate(bad)
        with self.assertRaises(ValueError): compiler.validate(dict(compiler.MOBILE,loader='bar'),True)
        with self.assertRaises(ValueError): compiler.validate(dict(compiler.MOBILE,unknown=True))

    def test_timeout_policy_is_validated_before_boot_writes(self):
        policy=str(ROOT/'config/boot/apply-grub-policy.sh')
        for timeout in range(11):
            result=subprocess.run(['bash',policy,'--timeout',str(timeout),'--print-policy'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn(f'timeout={timeout}\n',result.stdout)
            self.assertIn('timeout_style=hidden' if timeout==0 else 'timeout_style=menu',result.stdout)
        default=subprocess.check_output(['bash',policy,'--print-policy'],text=True)
        self.assertIn('timeout=5\n',default)
        for bad in ('-1','11','1.5','01','five','5;false'):
            result=subprocess.run(['bash',policy,'--timeout',bad],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIn('integer from 0 to 10',result.stderr)

    def test_font_license_is_packaged(self):
        spec = (ROOT/'packaging/rpm/luma-boot-theme.spec').read_text()
        self.assertIn('License:        Apache-2.0 AND MPL-2.0 AND OFL-1.1', spec)
        self.assertIn('%license %{_licensedir}/%{name}/OFL.txt', spec)
        self.assertIn('Requires:       google-figtree-fonts', spec)

if __name__ == '__main__': unittest.main()
