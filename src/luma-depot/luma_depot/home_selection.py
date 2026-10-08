# SPDX-License-Identifier: Apache-2.0
"""Choose real catalogue entries for Depot's live Discover page."""

from __future__ import annotations


EXPLORE_SLUGS = (
    "blender", "obsidian", "spotify", "signal", "vlc", "steam",
    "gimp", "discord", "libreoffice",
)
LUMA_SHELF_SLUGS = ("tide", "photos", "filer", "write", "viola")


def catalogue_slug(app) -> str:
    return app.slug or app.app_id.removeprefix("catalog:")


def featured_app(catalogue):
    featured = catalogue.featured
    if featured is not None and not featured.unlisted:
        return featured
    visible = tuple(app for app in catalogue.apps if not app.unlisted)
    return (next((app for app in visible if catalogue_slug(app) == "tide"), None)
            or next((app for app in visible if app.tier == "luma" and app.screenshots), None)
            or next((app for app in visible if app.tier == "luma"), None))


def luma_shelf_apps(catalogue, limit: int = 10):
    """Lead with the actual Luma apps in the v70 shelf, then fill from catalogue."""
    visible = tuple(app for app in catalogue.apps if app.tier == "luma" and not app.unlisted)
    priorities = {slug: index for index, slug in enumerate(LUMA_SHELF_SLUGS)}
    return tuple(sorted(visible, key=lambda app: (
        priorities.get(catalogue_slug(app), len(priorities)), app.name.casefold(),
        app.app_id))[:limit])


def popular_apps(catalogue, limit: int = 8):
    """Return entries and whether actual install counts support 'Popular'."""
    visible = tuple(app for app in catalogue.apps if not app.unlisted)
    ranked = sorted((app for app in visible if app.installs_total > 0),
                    key=lambda app: (-app.installs_total, app.name.casefold(), app.app_id))
    if len(ranked) >= limit:
        return tuple(ranked[:limit]), True

    by_slug = {catalogue_slug(app): app for app in visible}
    selected = list(ranked)
    seen = {app.app_id for app in selected}
    selected.extend(by_slug[slug] for slug in EXPLORE_SLUGS
                    if slug in by_slug and by_slug[slug].app_id not in seen)
    seen = {app.app_id for app in selected}
    selected.extend(app for app in visible if app.app_id not in seen and app.tier != "luma")
    return tuple(selected[:limit]), False
