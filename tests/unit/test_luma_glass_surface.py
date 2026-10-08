# SPDX-License-Identifier: Apache-2.0
"""One glass material, measured.

Owner, 2026-09-22, on three screenshots in a row: every application's title
bar was a slightly different colour; a Calendar month grid and sidebar showed
the wallpaper through them; and the calendar's dates and event chips could be
read through Quick Options standing on top of the window.

The three faults are one fault -- the material was decided in several places
and none of them was measured -- so these are the numbers the material must
meet, checked against the tokens every toolkit reads and against the files
generated from them. The rendered proof that four toolkits agree lives in
``src/luma-platform/tests/glass_surface_parity.py``, which runs in the
platform package's ``%check``; this holds the values that check compares.
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TOKENS = json.loads(
    (REPO_ROOT / "config/shared/design-tokens.json").read_text(encoding="utf-8"))
APPKIT = REPO_ROOT / "src/luma-platform/appkit"
TRANSLUCENT = ("frost", "glass")

# A paragraph of black-on-white text behind a surface must not read as text.
# Below about 1.5:1 a glyph is a smudge; 1.62:1, which an 80% veil gave, is
# still legible, and that is what the owner was looking at.
MAX_GHOST_CONTRAST = 1.5
# Body text on the surface, over the worst wallpaper there is.
MIN_INK_CONTRAST = 4.5


def _linear(channel: float) -> float:
    channel /= 255.0
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _luminance(colour: tuple[float, float, float]) -> float:
    red, green, blue = (_linear(channel) for channel in colour)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first, second) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _parse(colour: str) -> tuple[tuple[float, float, float], float]:
    """Return (rgb, alpha) for '#rrggbb' or 'rgba(r,g,b,.aa)'."""
    colour = colour.strip()
    hexadecimal = re.fullmatch(r"#([0-9a-fA-F]{6})", colour)
    if hexadecimal:
        digits = hexadecimal.group(1)
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4)), 1.0
    rgba = re.fullmatch(
        r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)", colour)
    if not rgba:
        raise AssertionError(f"cannot read colour {colour!r}")
    red, green, blue, alpha = rgba.groups()
    return (int(red), int(green), int(blue)), float(alpha)


def _over(colour: str, backdrop: tuple[float, float, float]):
    rgb, alpha = _parse(colour)
    return tuple(alpha * value + (1 - alpha) * behind
                 for value, behind in zip(rgb, backdrop))


BLACK = (0.0, 0.0, 0.0)
WHITE = (255.0, 255.0, 255.0)


class GlassMaterialTests(unittest.TestCase):
    """The values every toolkit reads."""

    def test_one_veil_per_treatment(self):
        """A context menu, Quick Options and an application header are one colour.

        Before this they were three: the title band was 37% on frost and 46%
        on glass, menus and sheets were 88% and 80%, and a second, weaker
        header tint (`chrome_secondary`) existed as well.
        """
        for treatment in TRANSLUCENT:
            recipe = TOKENS["surface_treatments"][treatment]
            veil = recipe["surface_veil"]
            with self.subTest(treatment=treatment):
                for role in ("chrome", "chrome_secondary", "menu"):
                    self.assertEqual(
                        recipe[role], veil,
                        f"{treatment}.{role} is {recipe[role]}, not the one veil {veil}")
                self.assertEqual(
                    TOKENS["appearance"][f"{treatment}_surface_veil"], veil)

    def test_generated_token_sheets_carry_the_one_veil(self):
        """The kit's sheets are generated from the tokens; prove they were."""
        for treatment in TRANSLUCENT:
            veil = TOKENS["surface_treatments"][treatment]["surface_veil"]
            sheet = (APPKIT / f"luma-appkit-{treatment}-tokens.css").read_text(
                encoding="utf-8")
            defined = dict(re.findall(
                r"@define-color\s+luma_(\w+)\s+([^;]+);", sheet))
            self.assertTrue(defined, f"no colours found in the {treatment} sheet")
            for role in ("chrome", "chrome_secondary", "menu"):
                with self.subTest(treatment=treatment, role=role):
                    self.assertEqual(defined[role].strip(), veil)

    def test_islands_are_opaque(self):
        """Nothing is read through an island, in either translucent treatment.

        The window's own surface is the glass -- the frame, the title band and
        the gaps between islands. What stands on it is paper, the same paper
        Light uses, so a month grid and a sidebar are solid.
        """
        light = TOKENS["light"]
        for treatment in TRANSLUCENT:
            recipe = TOKENS["surface_treatments"][treatment]
            with self.subTest(treatment=treatment):
                self.assertEqual(recipe["island"], light["island"])
                # Pane paper is an independently owned surface token; it need
                # not equal the white main island. The generated kit must use it.
                generated = (APPKIT / f"luma-appkit-{treatment}-tokens.css").read_text(encoding="utf-8")
                defined = dict(re.findall(r"@define-color\s+(\w+)\s+([^;]+);", generated))
                self.assertEqual(defined["luma_content"].strip(), recipe["pane"])
                self.assertEqual(recipe["surface"], light["card"])
                for role in ("island", "pane", "surface"):
                    _, alpha = _parse(recipe[role])
                    self.assertEqual(
                        alpha, 1.0,
                        f"{treatment}.{role} is translucent: {recipe[role]}")

    def test_the_window_surface_is_still_glass(self):
        """The frame stays see-through, or none of this is a material at all."""
        for treatment in TRANSLUCENT:
            frame = TOKENS["surface_treatments"][treatment]["frame"]
            _, alpha = _parse(frame)
            with self.subTest(treatment=treatment):
                self.assertLess(alpha, 1.0, f"{treatment} frame {frame} is opaque")
                self.assertGreater(
                    TOKENS["surface_treatments"][treatment]["blur_px"], 0)

    def test_text_behind_a_surface_does_not_read_as_text(self):
        """The reason the veil is as thick as it is.

        A Shell panel blurs the wallpaper behind it, not the windows, so over
        a window the veil's own alpha is the only thing between a reader and
        the paragraph underneath. This measures the worst case there is:
        black text on white paper directly behind the surface.
        """
        veils = {treatment: TOKENS["surface_treatments"][treatment]["surface_veil"]
                 for treatment in TRANSLUCENT}
        veils["dark"] = TOKENS["appearance"]["surface_veil_dark"]
        self.assertEqual(len(veils), 3)
        for name, veil in veils.items():
            with self.subTest(veil=name):
                ghost = _contrast(_over(veil, BLACK), _over(veil, WHITE))
                self.assertLessEqual(
                    ghost, MAX_GHOST_CONTRAST,
                    f"{name}: text behind the surface still reads at {ghost:.2f}:1")

    def test_ink_on_a_surface_holds_its_contrast(self):
        """A thicker veil must not be bought with unreadable text on it."""
        cases = [(treatment,
                  TOKENS["surface_treatments"][treatment]["surface_veil"],
                  TOKENS["surface_treatments"][treatment]["ink"])
                 for treatment in TRANSLUCENT]
        cases.append(("dark", TOKENS["appearance"]["surface_veil_dark"],
                      TOKENS["dark"]["ink"]))
        for name, veil, ink in cases:
            ink_rgb, _ = _parse(ink)
            worst = min(_contrast(ink_rgb, _over(veil, BLACK)),
                        _contrast(ink_rgb, _over(veil, WHITE)))
            with self.subTest(veil=name):
                self.assertGreaterEqual(
                    worst, MIN_INK_CONTRAST,
                    f"{name}: ink on the surface measures {worst:.2f}:1")

    def test_text_is_plain_on_a_translucent_surface(self):
        """No shadow and no outline anywhere on frost or glass."""
        structure = (REPO_ROOT / "src/luma-platform/ui/luma-ui.css").read_text(
            encoding="utf-8")
        self.assertIn("text-shadow: none", structure)
        self.assertIn("-gtk-icon-shadow: none", structure)
        patch = (REPO_ROOT / "patches/libadwaita"
                 / "0042-luma-one-glass-material-for-every-window.patch").read_text(
                     encoding="utf-8")
        self.assertIn("text-shadow: none;", patch)
        self.assertIn("-gtk-icon-shadow: none;", patch)

    def test_libadwaita_states_the_material_without_the_kit(self):
        """A rule naming a colour libadwaita does not define draws nothing.

        `@luma_chrome` was never defined in libadwaita, so the title band of
        Patch0040 was dropped in every application that did not load the kit's
        token sheet -- silently, which is why two windows in the same
        treatment were two colours.
        """
        patch = (REPO_ROOT / "patches/libadwaita"
                 / "0042-luma-one-glass-material-for-every-window.patch").read_text(
                     encoding="utf-8")
        for treatment in TRANSLUCENT:
            rgb, alpha = _parse(
                TOKENS["surface_treatments"][treatment]["surface_veil"])
            css = (f"--luma-veil: rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, "
                   f"{alpha:.2f});")
            with self.subTest(treatment=treatment):
                self.assertIn(css, patch,
                              f"libadwaita does not state the {treatment} veil")
        self.assertIn("linear-gradient(to bottom, var(--luma-veil), var(--luma-veil))",
                      patch)
        # And the class the rules select on reaches every window, not only a
        # kit window.
        self.assertIn("gtk_window_get_toplevels", patch)
        self.assertIn("luma_update_treatments", patch)

    def test_a_toolkit_that_cannot_participate_has_a_defined_stand_in(self):
        """GTK 3, and Chromium and Electron reading the GTK 3 theme."""
        expected = TOKENS["appearance"]["glass_header_opaque"]
        for sheet in ("gtk-translucent.css", "gtk4-translucent.css"):
            text = (REPO_ROOT / "src/luma-shell-state" / sheet).read_text(
                encoding="utf-8")
            with self.subTest(sheet=sheet):
                self.assertIn(f"@define-color luma_header_opaque {expected};", text)
        # It must be close to what a window that can be translucent shows, or
        # it is a defined mismatch rather than a defined match.
        stand_in, _ = _parse(expected)
        veil = TOKENS["surface_treatments"]["glass"]["surface_veil"]
        frame = TOKENS["surface_treatments"]["glass"]["frame"]
        window_colour, _ = _parse(TOKENS["light"]["window"])
        real = _over(veil, _over(frame, window_colour))
        difference = max(abs(a - b) for a, b in zip(stand_in, real))
        self.assertLessEqual(
            difference, 4,
            f"the stand-in {expected} is {difference} levels from the real "
            f"band {tuple(round(value) for value in real)}")


if __name__ == "__main__":
    unittest.main()
