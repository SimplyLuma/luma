# SPDX-License-Identifier: Apache-2.0
"""LumaUI rows family: the numbers its Python needs, from the token fragment.

The rows family's tokens live in `config/shared/design-tokens.d/rows.json`,
which the generator merges into the `lumaui` block: CSS reads them as
`--lumaui-<group>-<key>` (`--lumaui-nav-people-width`) and `@luma_<colour>`,
Python as `lumaui_tokens.<GROUP>` (`NAV_PEOPLE`). The few numbers Python lays
out with (a sidebar's width, a face's size) are read here, from the generated
module when it has them and otherwise from `_FALLBACK`, which
tests/unit/test_luma_appkit_lumaui_rows.py keeps equal to the fragment.

Private to the rows family.
"""
from __future__ import annotations

from . import lumaui_tokens

__all__: list[str] = []

_FALLBACK: dict[str, dict] = {
    "nav_people": {"width": 292},
    "nav_destinations": {"width": 224},
    "nav_resources": {"width": 248, "well": 32, "face": 34},
    "nav_files": {"width": 236},
    "nav_tree": {"width": 284, "indent": 16, "faces": 16},
    "nav_widths": {"narrow": 236, "regular": 272, "wide": 292},
    "row_lead": {"face_large": 46, "face_medium": 38, "face_small": 32, "group_scale": 0.7, "presence": 12,
                 "dot": 10, "dot_radius": 4, "dot_lightness_ratio": 0.7, "dot_chroma_ratio": 0.14},
    "avatar_stack": {"small": 16, "small_overlap": 5, "row": 18, "row_overlap": 6, "header": 28, "header_overlap": 8},
    "favourites": {"face": 48, "gap": 4, "column_count": 4, "wide_gap": 6, "wide_column_count": 3},
    "mark": {"box": 18, "large": 42, "button": 22, "halo_alpha": 0.22, "hue_lightness_ratio": 0.72,
             "hue_chroma_ratio": 0.13},
    "lit_glow": {"mask_start_pct": 35, "mask_end_pct": 90,
                 "dark": {"lightness_ratio": 0.5, "chroma_ratio": 0.13, "peak_alpha": 0.95, "second_alpha": 0.47},
                 "light": {"lightness_ratio": 0.85, "chroma_ratio": 0.1, "peak_alpha": 1.0, "second_alpha": 0.47},
                 "luma": ["rgba(254, 104, 25, 0.75)", "rgba(240, 86, 143, 0.6)", "rgba(255, 199, 154, 0.28)"]},
    "app_icon": {"corner_ratio": 0.22, "mono_size_ratio": 0.36, "mono_light_ratio": 0.74, "mono_dark_ratio": 0.46,
                 "mono_chroma_ratio": 0.12, "mono_tracking_em": -0.02},
    "type_scale": {"mono": {"size": 13.5, "weight": 400, "line_height": 1.55, "tracking_em": 0,
                            "family": "IBM Plex Mono"}},
}


def group(name: str) -> dict:
    """A token group by its fragment name (`"nav_people"`), generated values first."""
    generated = getattr(lumaui_tokens, name.upper(), None)
    if isinstance(generated, dict):
        return generated
    if name == "type_scale":
        merged = dict(_FALLBACK["type_scale"])
        merged.update(getattr(lumaui_tokens, "TYPE_SCALE", {}))
        return merged
    return _FALLBACK[name]


def value(name: str, key: str) -> float:
    """One number: `value("nav_people", "width")` is 292."""
    found = group(name).get(key)
    if found is None:
        found = _FALLBACK[name][key]
    return found
