# SPDX-License-Identifier: Apache-2.0
"""Explicit, disposable design fixtures. Never a report about this computer."""
from dataclasses import replace
from pathlib import Path
import os

from .appstream_catalogue import StaticCatalogProvider
from .os_updates import OsState
from .providers import Catalogue, Screenshot, Release, run_async


class PreviewCatalogue(StaticCatalogProvider):
    def _read(self):
        catalogue = super()._read()
        root = Path(os.environ.get("LUMA_DEPOT_PREVIEW_ASSETS", "/nonexistent"))
        apps = []
        for app in catalogue.apps:
            # Legacy labels and example numbers belong only to this fixture.
            if app.app_id == "io.luma.Inkline":
                app = replace(app, tagline="A small, quick drawing tool.",
                              banner=str(root / "prism.png"), tone="blue")
            if app.app_id == "io.luma.Reel":
                app = replace(app, screenshots=tuple(
                    Screenshot((root / "ember.png").as_uri(), "Illustrative design artwork")
                    for _ in range(3)),
                    releases=(Release("0.1.0", "2 weeks ago"),),
                    description="Reel is the video editor in the Fable suite. Each row of your timeline decides for itself whether clips snap together or stay exactly where you put them, so a locked story and a loose B-roll lane can live side by side.")
            # Fixture tiers and a review slug, so the preview reaches every
            # state of the app page. None of this describes a real listing.
            app = replace(app, slug=app.app_id.rsplit(".", 1)[-1].lower(),
                          tier={"io.luma.Reel": "luma", "io.luma.Inkline": "luma",
                                "io.luma.Studio": "verified"}.get(app.app_id, "listed"),
                          developer_verified="organization" if app.app_id in {
                              "io.luma.Reel", "io.luma.Inkline", "io.luma.Studio"} else "",
                          sign_in="none" if app.app_id != "io.luma.Bellwether" else "required-third-party")
            if app.app_id in {"io.luma.Studio"}:
                app = replace(app, tone="violet")
            if app.app_id == "io.luma.Ledgerbook":
                app = replace(app, tone="green")
            if app.app_id == "io.luma.Crate":
                app = replace(app, download_bytes=64_000_000,
                              summary="Run test machines without the command line.", tone="amber")
            apps.append(app)
        return Catalogue(tuple(apps), catalogue.categories,
                         next((a for a in apps if a.featured), None))


def preview_reviews(app):
    """Illustrative reviews for the preview only. Never shown in native mode."""
    from luma_installer.depot_reviews import Reply, Review, ReviewPage
    if app.rating_count < 3:
        return ReviewPage(app.rating, app.rating_count, ())
    return ReviewPage(app.rating, app.rating_count, (
        Review("preview-1", 5, "Does one thing and does it well", "Opens instantly and stays out of the way. "
               "The row-by-row snapping is the feature I did not know I wanted.", "Preview reviewer",
               app.version or "0.1.0", True, "2026-09-02T10:00:00Z", "2026-09-02T10:00:00Z",
               reply=Reply("Thank you. Snapping per row came from exactly this kind of note.",
                           app.developer, "2026-09-03T09:00:00Z")),
        Review("preview-2", 4, "Nearly there", "I would like a way to export straight to a shared folder.",
               "Another preview reviewer", app.version or "0.1.0", False,
               "2026-08-28T18:30:00Z", "2026-08-28T18:30:00Z"),
    ))


class PreviewOsReader:
    """Static update-ready fixture; no shell command, timer or machine writes."""
    def state(self, callback, cancellable=None):
        run_async(lambda: OsState(
            enrolled=True, booted_version="0.1", staged_version="Fable Prairie 0.2",
            staged_bytes=271_000_000,
            notes=("This month’s security fixes", "Quieter fans under heavy load",
                   "Dock and window placement fixes", "A new overview, opened with the Super key")),
            callback, cancellable)


def preview_release_notes():
    """The preview's "What's new": illustrative notes, read like real ones."""
    from .release_notes import parse
    return parse({
        "schema": "org.projectluma.os-release-notes/v2",
        "display_name": "Fable Prairie 0.2", "nightly_date": "2026-09-02",
        "sections": {
            "feature": [{"summary": "A new overview for your windows and apps",
                         "details": "Press the Super key to see every open window and your apps at a glance."}],
            "improvement": [{"summary": "Quieter fans under heavy load",
                             "details": "Fan curves ramp more gently while the processor is busy."}],
            "fix": [{"summary": "Fixed where new windows open",
                     "details": "New windows open on the screen you are working on."}],
            "security": [{"summary": "This month’s security fixes",
                          "details": "Includes the latest fixes for the kernel and the web engine."}],
        },
    })
