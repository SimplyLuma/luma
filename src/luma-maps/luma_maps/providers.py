# SPDX-License-Identifier: Apache-2.0

"""Where the map, the search and the routes come from.

Every endpoint is configuration. The application ships defaults in
`data/providers.toml` and reads an override from the user's config directory,
so pointing Maps at a self-hosted stack — or at a local archive — is an edit,
not a rebuild.

`local` is load-bearing. The footer chip in the design promises "This map is on
your machine", and that promise is only made when the active provider actually
sets this flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import pathlib
import tomllib


@dataclass(frozen=True)
class TileProvider:
    id: str
    name: str
    kind: str                     # "vector", "raster" or "pmtiles"
    local: bool
    attribution: str
    style_url: str = ""
    url_template: str = ""
    archive: str = ""
    min_zoom: int = 0
    max_zoom: int = 19
    tile_size: int = 256
    development_only: bool = False
    treatment: str = ""       # "light", "dark", or empty for either

    @property
    def usable(self) -> bool:
        """Whether this provider can actually draw a map right now."""
        if self.kind == "pmtiles":
            return bool(self.archive) and pathlib.Path(self.archive).expanduser().is_file()
        if self.kind == "vector":
            return bool(self.style_url)
        return bool(self.url_template)

    @property
    def host(self) -> str:
        source = self.style_url or self.url_template
        if "//" not in source:
            return ""
        return source.split("//", 1)[1].split("/", 1)[0]


@dataclass(frozen=True)
class SearchProvider:
    autocomplete_url: str
    geocode_url: str
    reverse_url: str
    autocomplete_debounce_ms: int
    geocode_min_interval_s: float
    local: bool


@dataclass(frozen=True)
class RoutingProvider:
    engine: str
    url: str
    modes: tuple[str, ...]
    local: bool
    development_only: bool


@dataclass(frozen=True)
class Providers:
    user_agent: str
    tiles: tuple[TileProvider, ...]
    search: SearchProvider
    routing: RoutingProvider
    default_tile_id: str

    def tile(self, tile_id: str) -> TileProvider | None:
        for provider in self.tiles:
            if provider.id == tile_id:
                return provider
        return None

    def counterpart(self, provider: TileProvider, treatment: str) -> TileProvider:
        """The same map drawn for the other treatment, when there is one."""
        if not provider.treatment or provider.treatment == treatment:
            return provider
        for candidate in self.tiles:
            if (candidate.treatment == treatment and candidate.kind == provider.kind
                    and candidate.local == provider.local and candidate.usable):
                return candidate
        return provider

    def for_treatment(self, treatment: str) -> TileProvider:
        return self.counterpart(self.default_tile, treatment)

    @property
    def default_tile(self) -> TileProvider:
        chosen = self.tile(self.default_tile_id)
        if chosen is not None and chosen.usable:
            return chosen
        # A local archive is preferred whenever one is actually present: the
        # honest answer to "where is this map" should be "here" when it can be.
        for provider in self.tiles:
            if provider.local and provider.usable:
                return provider
        for provider in self.tiles:
            if provider.usable:
                return provider
        return self.tiles[0]


def _config_paths() -> list[pathlib.Path]:
    here = pathlib.Path(__file__).resolve().parent.parent
    shipped = os.environ.get("LUMA_MAPS_PROVIDERS", "") or str(here / "data/providers.toml")
    config_home = os.environ.get("XDG_CONFIG_HOME", "") or str(pathlib.Path.home() / ".config")
    return [pathlib.Path(shipped), pathlib.Path(config_home) / "luma-maps/providers.toml"]


def _merge(base: dict, extra: dict) -> dict:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load() -> Providers:
    data: dict = {}
    for path in _config_paths():
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            data = _merge(data, tomllib.load(handle))

    tiles: list[TileProvider] = []
    default_id = ""
    for entry in data.get("tiles", []):
        provider = TileProvider(
            id=entry.get("id", ""),
            name=entry.get("name", entry.get("id", "Map")),
            kind=entry.get("kind", "raster"),
            local=bool(entry.get("local", False)),
            attribution=entry.get("attribution", "© OpenStreetMap contributors"),
            style_url=entry.get("style_url", ""),
            url_template=entry.get("url_template", ""),
            archive=entry.get("archive", ""),
            min_zoom=int(entry.get("min_zoom", 0)),
            max_zoom=int(entry.get("max_zoom", 19)),
            tile_size=int(entry.get("tile_size", 256)),
            development_only=bool(entry.get("development_only", False)),
            treatment=entry.get("treatment", ""),
        )
        tiles.append(provider)
        if entry.get("default"):
            default_id = provider.id

    search = data.get("search", {})
    routing = data.get("routing", {})
    return Providers(
        user_agent=data.get("identity", {}).get("user_agent", "LumaMaps/0.1"),
        tiles=tuple(tiles),
        default_tile_id=default_id or (tiles[0].id if tiles else ""),
        search=SearchProvider(
            autocomplete_url=search.get("autocomplete_url", ""),
            geocode_url=search.get("geocode_url", ""),
            reverse_url=search.get("reverse_url", ""),
            autocomplete_debounce_ms=int(search.get("autocomplete_debounce_ms", 350)),
            geocode_min_interval_s=float(search.get("geocode_min_interval_s", 1.0)),
            local=bool(search.get("local", False)),
        ),
        routing=RoutingProvider(
            engine=routing.get("engine", ""),
            url=routing.get("url", ""),
            modes=tuple(routing.get("modes", ("drive", "walk", "cycle"))),
            local=bool(routing.get("local", False)),
            development_only=bool(routing.get("development_only", False)),
        ),
    )
