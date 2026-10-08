# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os

from luma_appkit import add_style_sheet, install_appkit


def install_theme() -> None:
    """Install the kit, then Prairie's own application rules.

    The kit is the theme now. What remains in prairie.css is what its
    applications draw that nothing else does — the camera's viewfinder, the
    dialler's keys, the weather's temperature — named in the kit's colours.
    """
    install_appkit()
    path = os.environ.get("PRAIRIE_STYLE_PATH", "/usr/share/prairie-core/prairie.css")
    if os.path.isfile(path):
        add_style_sheet(path)
