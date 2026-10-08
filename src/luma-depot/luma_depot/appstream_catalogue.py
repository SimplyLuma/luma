# SPDX-License-Identifier: Apache-2.0

"""A catalogue read from AppStream.

The placeholder data is a real AppStream collection file, and this reads it the
way a Flatpak remote's AppStream branch would be read — through libappstream
where the introspection is available, and through the same XML directly where
it is not. Either way the parsing path is exercised now, so the day the file
comes from a repository instead of the disk, nothing above this changes.
"""

from __future__ import annotations

import os
import pathlib
import time
import xml.etree.ElementTree as ElementTree

import gi

from luma_installer import depot_icons

from .providers import (
    App, Catalogue, CatalogProvider, Category, FaultInjector, Permission,
    ProviderError, Release, Screenshot, run_async,
)


CATEGORIES = (
    Category("Create", "Create", "lumaui-palette-symbolic"),
    Category("Work", "Work", "lumaui-briefcase-symbolic"),
    Category("Media", "Media", "lumaui-clapperboard-symbolic"),
    Category("Play", "Play", "lumaui-gamepad-2-symbolic"),
    Category("Tools", "Tools", "lumaui-wrench-symbolic"),
)

# AppStream's categories are the standard freedesktop set; Luma groups them into
# four shelves. The mapping lives here rather than in the data so a real
# repository's components land on the right shelf without being re-tagged.
_GROUPS = {
    "Graphics": "Create", "AudioVideo": "Create", "Audio": "Create", "Video": "Create",
    "Office": "Work", "Development": "Work", "Network": "Work",
    "Game": "Play",
    "Utility": "Tools", "System": "Tools", "Settings": "Tools",
}


def catalogue_path() -> pathlib.Path:
    override = os.environ.get("LUMA_DEPOT_CATALOGUE", "")
    if override:
        return pathlib.Path(override)
    packaged = pathlib.Path("/usr/share/luma-depot/catalogue/luma-depot-placeholder.xml")
    if packaged.is_file():
        return packaged
    return (pathlib.Path(__file__).resolve().parent.parent
            / "data/catalogue/luma-depot-placeholder.xml")


class StaticCatalogProvider(CatalogProvider):
    """The catalogue as it exists before there is a repository.

    Answers are still delivered on a worker thread and still take a moment,
    because the interface above has to be built against a provider that behaves
    like a network one.
    """

    def __init__(self, faults: FaultInjector | None = None) -> None:
        self.faults = faults or FaultInjector()
        self._cache: Catalogue | None = None

    # ── Reading ──────────────────────────────────────────────────────────

    def _delay(self, seconds: float) -> None:
        time.sleep(seconds * (6.0 if self.faults.enabled("slow") else 1.0))

    def _read(self) -> Catalogue:
        self.faults.check_reachable()
        self._delay(0.25)
        if self.faults.enabled("empty"):
            return Catalogue(apps=(), categories=CATEGORIES, featured=None)
        if self._cache is not None:
            return self._cache
        path = catalogue_path()
        if not path.is_file():
            raise ProviderError(
                f"no catalogue at {path}",
                hint="Depot has no catalogue to show. The placeholder catalogue is "
                     "missing from this installation.",
            )
        apps = _parse_with_appstream(path)
        if apps is None:
            apps = _parse_with_elementtree(path)
        if not apps:
            raise ProviderError(
                "the catalogue parsed to nothing",
                hint="The catalogue could be read but contained no applications.",
            )
        featured = next((app for app in apps if app.featured), apps[0])
        self._cache = Catalogue(apps=apps, categories=CATEGORIES, featured=featured)
        return self._cache

    # ── The interface ────────────────────────────────────────────────────

    def load_catalogue(self, callback, cancellable=None) -> None:
        run_async(self._read, callback, cancellable)

    def search(self, query: str, callback, cancellable=None) -> None:
        text = query.strip().casefold()

        def work():
            catalogue = self._read()
            self._delay(0.12)
            if not text:
                return ()
            return tuple(
                app for app in catalogue.apps
                if text in app.name.casefold()
                or text in app.summary.casefold()
                or text in app.description.casefold()
            )

        run_async(work, callback, cancellable)

    def app(self, app_id: str, callback, cancellable=None) -> None:
        def work():
            catalogue = self._read()
            found = catalogue.find(app_id)
            if found is None:
                raise ProviderError(
                    f"no component {app_id}",
                    hint="That application is no longer in the catalogue.",
                )
            return found

        run_async(work, callback, cancellable)


# ── Parsing ──────────────────────────────────────────────────────────────

def _custom_values(node) -> dict[str, str]:
    values: dict[str, str] = {}
    for custom in node.findall("custom"):
        for value in custom.findall("value"):
            key = value.get("key") or ""
            if key:
                values[key] = (value.text or "").strip()
    return values


def _permissions(values: dict[str, str]) -> tuple[Permission, ...]:
    found = []
    for key, raw in values.items():
        if not key.startswith("luma::permission::"):
            continue
        parts = raw.split("|")
        if len(parts) < 2:
            continue
        found.append(Permission(
            key=key.rsplit("::", 1)[-1],
            title=parts[0],
            detail=parts[1],
            notable=len(parts) > 2 and parts[2] == "notable",
        ))
    found.sort(key=lambda item: item.title)
    return tuple(found)


def _to_app(*, app_id, name, summary, description, developer, categories, icon,
            download, installed, licence, rating, ratings, screenshots, releases,
            values) -> App:
    group = values.get("luma::group") or next(
        (_GROUPS[category] for category in categories if category in _GROUPS), "Tools")
    return App(
        app_id=app_id,
        name=name,
        summary=summary,
        description=description,
        developer=developer or "Unknown developer",
        categories=(group,),
        icon_name=icon or "application-x-executable-symbolic",
        tone=values.get("luma::tone", "blue"),
        download_bytes=download,
        installed_bytes=installed,
        licence=licence,
        age_rating=values.get("luma::age-rating", ""),
        rating=float(values.get("luma::rating", 0) or 0),
        rating_count=int(values.get("luma::rating-count", 0) or 0),
        screenshots=screenshots,
        releases=releases,
        permissions=_permissions(values),
        featured=values.get("luma::featured", "") == "true",
        tagline=values.get("luma::tagline", ""),
    )


def _parse_with_appstream(path: pathlib.Path) -> tuple[App, ...] | None:
    """Parse through libappstream, which is what a real remote is read with."""
    try:
        gi.require_version("AppStream", "1.0")
        from gi.repository import AppStream
    except (ImportError, ValueError):
        return None
    try:
        metadata = AppStream.Metadata.new()
        metadata.set_format_style(AppStream.FormatStyle.CATALOG)
        metadata.parse_file(_gio_file(str(path)), AppStream.FormatKind.XML)
        components = metadata.get_components()
        entries = components.as_array() if hasattr(components, "as_array") else components
    except Exception:
        return None

    apps: list[App] = []
    # The custom keys and sizes are read from the XML alongside, because the
    # introspected accessors for them differ between AppStream releases and a
    # wrong guess would silently drop a field.
    raw = {node.findtext("id"): node for node in ElementTree.parse(path).getroot()}
    for component in entries:
        try:
            app_id = component.get_id()
            node = raw.get(app_id)
            values = _custom_values(node) if node is not None else {}
            screenshots = tuple(
                Screenshot(url=url, caption=caption)
                for url, caption in _screenshots_from_xml(node)
            ) if node is not None else ()
            releases = tuple(
                Release(version=version, date=date, description=text)
                for version, date, text in _releases_from_xml(node)
            ) if node is not None else ()
            sizes = _sizes_from_xml(node) if node is not None else (0, 0)
            apps.append(_to_app(
                app_id=app_id,
                name=component.get_name() or app_id,
                summary=component.get_summary() or "",
                description=_plain(component.get_description() or ""),
                developer=_developer(component, node),
                categories=tuple(component.get_categories() or ()),
                icon=_icon_from_xml(node),
                download=sizes[0], installed=sizes[1],
                licence=component.get_project_license() or "",
                rating=0.0, ratings=0,
                screenshots=screenshots, releases=releases, values=values,
            ))
        except Exception:
            continue
    return tuple(apps) if apps else None


def _gio_file(path: str):
    from gi.repository import Gio

    return Gio.File.new_for_path(path)


def _plain(markup: str) -> str:
    text = markup.replace("</p>", "\n\n").replace("<p>", "")
    while "<" in text and ">" in text:
        start = text.index("<")
        end = text.index(">", start)
        text = text[:start] + text[end + 1:]
    return " ".join(text.split())


def _developer(component, node) -> str:
    for reader in ("get_developer_name",):
        try:
            value = getattr(component, reader)()
            if value:
                return value
        except Exception:
            pass
    if node is not None:
        developer = node.find("developer")
        if developer is not None:
            return developer.findtext("name") or ""
    return ""


def _icon_from_xml(node, directory: pathlib.Path | None = None) -> str:
    """The icon one component names: a theme name, or a path to a file.

    AppStream's three spellings are not interchangeable. ``stock`` is an icon
    theme name. ``local`` is already a path. ``cached`` is a bare file name
    belonging to the collection file's own ``icons`` directory -- and handing
    that to a toolkit as a themed icon name finds nothing, raises nothing, and
    draws the toolkit's missing-image glyph. So each is resolved as what it
    is, and a ``cached`` name whose file is not there yields nothing, which
    the window draws as the application's monogram.
    """

    base = directory if directory is not None else catalogue_path().parent
    for icon in node.findall("icon"):
        kind = icon.get("type")
        value = (icon.text or "").strip()
        if not value:
            continue
        if kind == "stock":
            return value
        if kind == "local" and depot_icons.looks_like_a_path(value):
            return value
        if kind == "cached":
            found = depot_icons.appstream_icon(base, value, size=128)
            if found is not None:
                return str(found)
    return ""


def _sizes_from_xml(node) -> tuple[int, int]:
    download = installed = 0
    for size in node.findall("size"):
        try:
            value = int((size.text or "0").strip())
        except ValueError:
            continue
        if size.get("type") == "download":
            download = value
        elif size.get("type") == "installed":
            installed = value
    return download, installed


def _screenshots_from_xml(node):
    for shot in node.findall("./screenshots/screenshot"):
        image = shot.find("image")
        if image is None or not (image.text or "").strip():
            continue
        yield (image.text or "").strip(), shot.findtext("caption") or ""


def _releases_from_xml(node):
    for release in node.findall("./releases/release"):
        yield (release.get("version") or "",
               release.get("date") or "",
               _plain(ElementTree.tostring(release.find("description"), encoding="unicode")
                      if release.find("description") is not None else ""))


def _parse_with_elementtree(path: pathlib.Path) -> tuple[App, ...]:
    """The same file, without libappstream. Kept so Depot runs anywhere."""
    apps: list[App] = []
    for node in ElementTree.parse(path).getroot():
        app_id = node.findtext("id") or ""
        if not app_id:
            continue
        values = _custom_values(node)
        download, installed = _sizes_from_xml(node)
        apps.append(_to_app(
            app_id=app_id,
            name=node.findtext("name") or app_id,
            summary=node.findtext("summary") or "",
            description=_plain(ElementTree.tostring(node.find("description"), encoding="unicode")
                               if node.find("description") is not None else ""),
            developer=(node.find("developer").findtext("name")
                       if node.find("developer") is not None else ""),
            categories=tuple(category.text or "" for category in node.findall("./categories/category")),
            icon=_icon_from_xml(node),
            download=download, installed=installed,
            licence=node.findtext("project_license") or "",
            rating=0.0, ratings=0,
            screenshots=tuple(Screenshot(url=url, caption=caption)
                              for url, caption in _screenshots_from_xml(node)),
            releases=tuple(Release(version=version, date=date, description=text)
                           for version, date, text in _releases_from_xml(node)),
            values=values,
        ))
    return tuple(apps)
