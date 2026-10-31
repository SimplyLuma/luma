# SPDX-License-Identifier: Apache-2.0
"""Keycap has one design source: config/shared/design-tokens.json `keycap`.

Stevia (phosh images) and the GNOME Shell keyboard (every Shell device) must
paint the same palette and geometry. Stevia's sheets live inside its patch, so
they are read back from it; the Shell's sheet, tokens module and icons are
generated and must be current.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOKENS = json.loads((ROOT / "config/shared/design-tokens.json").read_text(encoding="utf-8"))["keycap"]
STEVIA = (ROOT / "patches/stevia/0004-luma-keycap-input-tools.patch").read_text(encoding="utf-8")
SHELL_PATCH = (ROOT / "patches/gnome-shell/0294-keycap-keyboard.patch").read_text(encoding="utf-8")
SPEC = (ROOT / "patches/gnome-shell/0000-luma-fedora-spec.patch").read_text(encoding="utf-8")
BUILDER = (ROOT / "scripts/packages/build-gnome-shell.sh").read_text(encoding="utf-8")
# Stevia's @define-color names for the shared tokens.
STEVIA_NAMES = {"pos_key_fg_color": "ink"}


def stevia_sheet(name: str) -> str:
    match = re.search(r"^\+\+\+ b/src/stylesheet/%s\n((?:[ +\-@][^\n]*\n)*)" % re.escape(name), STEVIA, re.M)
    assert match, name
    return "".join(line[1:] + "\n" for line in match.group(1).splitlines()
                   if line.startswith("+") or line.startswith(" "))


def canonical(colour: str) -> str:
    colour = colour.replace(" ", "").lower()
    if re.fullmatch(r"#[0-9a-f]{3}", colour):
        colour = "#" + "".join(c * 2 for c in colour[1:])
    return re.sub(r"(?<=[,(])\.", "0.", colour)


class KeycapTokens(unittest.TestCase):
    def test_stevia_palettes_are_the_tokens(self):
        for mode in ("light", "dark"):
            defined = dict(re.findall(r"@define-color (\w+) ([^;]+);", stevia_sheet(f"luma-{mode}.css")))
            for name, value in defined.items():
                token = STEVIA_NAMES.get(name, name)
                if token in TOKENS[mode]:
                    self.assertEqual(canonical(value), canonical(TOKENS[mode][token]), f"{mode} {name}")
            for token in ("tray_top", "cap_top", "fn_top", "down_top", "edge", "enter_top", "ink"):
                self.assertIn(token, {STEVIA_NAMES.get(name, name) for name in defined}, f"{mode} {token}")

    def test_stevia_geometry_is_the_tokens(self):
        sheet = stevia_sheet("luma-keycap.css")
        geometry = TOKENS["geometry"]
        self.assertIn(f"border-radius: {geometry['radius']}px", sheet)
        self.assertIn(f"font-size: {geometry['letter_size']}px", sheet)
        self.assertIn(f"min-height: {geometry['strip_height']}px", sheet)
        self.assertIn(f"0 {geometry['edge']}px 0 @edge", sheet)
        self.assertIn(f"0 {geometry['edge'] - geometry['travel']}px 0 @edge", sheet)
        self.assertIn("margin: 4px 2.5px 5px", sheet)
        self.assertEqual(geometry["gutter_top"], 4)
        self.assertEqual(geometry["gutter_x"], 5)
        self.assertEqual(geometry["gutter_bottom"], 5)
        self.assertIn(f"#define BS_KEY_REPEAT_DELAY {TOKENS['timing']['delete_repeat_delay']}", STEVIA)
        self.assertIn(f"#define MAX_CLIPS {TOKENS['clipboard']['max_clips']}", STEVIA)
        self.assertIn(f"> {TOKENS['clipboard']['max_bytes']}", STEVIA)

    def test_shell_outputs_are_generated(self):
        for script in ("generate-luma-platform-tokens.py", "generate-keycap-shell-icons.py"):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/developer" / script), "--check"],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shell_patch_is_registered(self):
        self.assertIn("Patch1274:      0294-keycap-keyboard.patch", SPEC)
        self.assertIn("0294-keycap-keyboard.patch", BUILDER)
        self.assertIn("keycap.js keycapTokens.js keycap.css", BUILDER)
        self.assertIn("gjs -m %{SOURCE9033} js/ui/lumaPresentation/keycap.js js/ui/keyboard.js", SPEC)
        for name in ("keycap.js", "keycapTokens.js", "keycap.css"):
            self.assertIn(f"<file>ui/lumaPresentation/{name}</file>", SHELL_PATCH)
        icons = sorted(path.name for path in (ROOT / "src/luma-shell-presentation/keycap-icons").glob("*.svg"))
        for icon in icons:
            self.assertIn(f"scalable/status/{icon}</file>", SHELL_PATCH)
        # The Shell keys share Stevia's preference names and defaults.
        schema = re.search(r'<schema id="mobi.phosh.osk"[^\n]*\n((?:[^\n]*\n)*?)', STEVIA)
        for key, default in re.findall(r'<key name="([\w-]+)" type="b"><default>(\w+)</default>', STEVIA):
            self.assertRegex(SHELL_PATCH, rf'<key name="{key}" type="b">\n\+\s*<default>{default}</default>')
        self.assertIsNotNone(schema)


if __name__ == "__main__":
    unittest.main()
