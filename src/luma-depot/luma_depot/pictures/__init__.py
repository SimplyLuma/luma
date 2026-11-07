# SPDX-License-Identifier: Apache-2.0
"""Every picture Depot shows, framed in a Luma setting (handoff 9)."""
from __future__ import annotations


def pictures_for(world, item) -> list:
    """An app's framed screenshots; a Look's and a Tweak's composed pictures."""
    if item.kind == "app":
        from .frames import app_pictures
        return app_pictures(world, item)
    try:
        from .composed import composed_pictures
    except ImportError:  # the Look and Tweak pictures arrive with step 4
        return []
    return composed_pictures(world, item)
