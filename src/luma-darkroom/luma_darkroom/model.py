# SPDX-License-Identifier: Apache-2.0
"""Versioned, non-destructive Darkroom document model.

Source pixels and derived preview caches never enter the document JSON.  The
document stores a durable source reference plus an ordered, editable recipe.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


CURRENT_SCHEMA_VERSION = 1
DOCUMENT_SUFFIX = ".luma-darkroom"
LAYER_KINDS = frozenset({"raw", "pixel", "adjustment", "fill", "text", "shape", "embedded", "linked", "group"})
MASK_KINDS = frozenset({"brush", "linear-gradient", "radial-gradient", "luminance-range", "color-range", "subject", "sky", "shape"})
MASK_OPERATIONS = frozenset({"add", "subtract", "intersect"})
BLEND_MODES = frozenset({"normal", "multiply", "screen", "overlay", "soft-light", "hard-light", "darken", "lighten", "color-dodge", "color-burn", "difference", "hue", "saturation", "color", "luminosity"})
ARRANGEMENTS = frozenset({"develop", "edit", "retouch", "review"})


class DocumentError(RuntimeError):
    pass


class InvalidDocument(DocumentError):
    pass


class UnsupportedDocumentVersion(DocumentError):
    def __init__(self, found: int) -> None:
        super().__init__(f"This Darkroom document uses format {found}; this version supports up to {CURRENT_SCHEMA_VERSION}.")
        self.found = found


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def file_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme not in {"", "file"}:
        return None
    return Path(unquote(parsed.path if parsed.scheme else uri))


def source_identity(uri: str) -> tuple[str, dict[str, Any]]:
    path = file_path(uri)
    digest = hashlib.sha256()
    relink: dict[str, Any] = {"original_uri": uri}
    if path is None:
        digest.update(uri.encode("utf-8", "surrogateescape"))
        relink["display_name"] = uri.rsplit("/", 1)[-1] or uri
        return digest.hexdigest(), relink
    resolved = path.expanduser().resolve(strict=False)
    relink["display_name"] = resolved.name
    try:
        stat = resolved.stat()
    except OSError:
        digest.update(str(resolved).encode("utf-8", "surrogateescape"))
    else:
        relink.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns, device=stat.st_dev, inode=stat.st_ino)
        digest.update(f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest(), relink


@dataclass(slots=True)
class Source:
    uri: str
    identity: str
    display_name: str
    content_type: str = ""
    width: int = 0
    height: int = 0
    bit_depth: int = 8
    embedded_profile: str = "sRGB"
    raw: bool = False
    relink: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_uri(cls, uri: str, **metadata: Any) -> "Source":
        identity, relink = source_identity(uri)
        return cls(uri=uri, identity=identity, display_name=str(metadata.pop("display_name", relink.get("display_name", "Untitled image"))), relink=relink, **metadata)

    @property
    def missing(self) -> bool:
        path = file_path(self.uri)
        return path is not None and not path.exists()

    def validate(self) -> None:
        if not self.uri or not self.identity or not self.display_name:
            raise InvalidDocument("source requires URI, identity, and display name")
        if self.width < 0 or self.height < 0 or self.bit_depth not in {8, 10, 12, 14, 16, 32}:
            raise InvalidDocument("source dimensions or bit depth are invalid")


@dataclass(slots=True)
class Adjustment:
    id: str
    kind: str
    value: float | int | str | bool | list[float] | list[list[float]]
    enabled: bool = True
    amount: float = 1.0

    def validate(self) -> None:
        if not self.id or not self.kind:
            raise InvalidDocument("adjustment requires id and kind")
        if not 0.0 <= self.amount <= 1.0:
            raise InvalidDocument(f"adjustment {self.id} amount is outside 0…1")


@dataclass(slots=True)
class Mask:
    id: str
    name: str
    kind: str
    operation: str = "add"
    enabled: bool = True
    inverted: bool = False
    feather: float = 0.0
    density: float = 1.0
    overlay_color: str = "#ef5f6b"
    overlay_opacity: float = 0.45
    geometry: dict[str, Any] = field(default_factory=dict)
    children: list["Mask"] = field(default_factory=list)

    def validate(self) -> None:
        if self.kind not in MASK_KINDS or self.operation not in MASK_OPERATIONS:
            raise InvalidDocument(f"mask {self.id} has an unsupported kind or operation")
        if not self.id or not self.name:
            raise InvalidDocument("mask requires id and name")
        if not 0.0 <= self.feather <= 1.0 or not 0.0 <= self.density <= 1.0 or not 0.0 <= self.overlay_opacity <= 1.0:
            raise InvalidDocument(f"mask {self.id} has an invalid range")
        for child in self.children:
            child.validate()


@dataclass(slots=True)
class Transform:
    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0
    rotation: float = 0.0
    skew_x: float = 0.0
    skew_y: float = 0.0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    aspect_locked: bool = True

    def validate(self) -> None:
        if self.width <= 0.0 or self.height <= 0.0:
            raise InvalidDocument("transform dimensions must be positive")


@dataclass(slots=True)
class Layer:
    id: str
    name: str
    kind: str
    visible: bool = True
    locked: bool = False
    opacity: float = 1.0
    blend_mode: str = "normal"
    source_uri: str | None = None
    embedded: bool = False
    adjustments: list[Adjustment] = field(default_factory=list)
    masks: list[Mask] = field(default_factory=list)
    children: list["Layer"] = field(default_factory=list)
    transform: Transform = field(default_factory=Transform)
    text: dict[str, Any] | None = None
    missing_resource: bool = False

    def validate(self) -> None:
        if self.kind not in LAYER_KINDS or self.blend_mode not in BLEND_MODES:
            raise InvalidDocument(f"layer {self.id} has an unsupported kind or blend mode")
        if not self.id or not self.name or not 0.0 <= self.opacity <= 1.0:
            raise InvalidDocument(f"layer {self.id} has invalid identity or opacity")
        self.transform.validate()
        for adjustment in self.adjustments:
            adjustment.validate()
        for mask in self.masks:
            mask.validate()
        for child in self.children:
            child.validate()


@dataclass(slots=True)
class Crop:
    left: float = 0.0
    top: float = 0.0
    right: float = 1.0
    bottom: float = 1.0
    rotation: float = 0.0
    straighten: float = 0.0
    perspective_x: float = 0.0
    perspective_y: float = 0.0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    overlay: str = "thirds"

    def validate(self) -> None:
        if not (0.0 <= self.left < self.right <= 1.0 and 0.0 <= self.top < self.bottom <= 1.0):
            raise InvalidDocument("crop bounds must form a normalized rectangle")
        if self.overlay not in {"none", "thirds", "golden", "diagonal"}:
            raise InvalidDocument("crop overlay is invalid")


@dataclass(slots=True)
class RetouchOperation:
    id: str
    kind: str
    points: list[list[float]]
    source: list[float] | None = None
    size: float = 0.03
    feather: float = 0.5
    opacity: float = 1.0
    sample_mode: str = "all-below"
    enabled: bool = True

    def validate(self) -> None:
        if self.kind not in {"heal", "clone", "remove", "red-eye", "dodge", "burn", "blur", "sharpen"}:
            raise InvalidDocument(f"retouch operation {self.id} is unsupported")
        if not self.points or not 0.0 < self.size <= 1.0 or not 0.0 <= self.feather <= 1.0 or not 0.0 <= self.opacity <= 1.0:
            raise InvalidDocument(f"retouch operation {self.id} has invalid geometry")


@dataclass(slots=True)
class HistoryEntry:
    id: str
    name: str
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Snapshot:
    id: str
    name: str
    created_at: float
    recipe: dict[str, Any]


@dataclass(slots=True)
class WorkspaceArrangement:
    left_visible: bool = True
    secondary_panel: str = "layers"
    secondary_visible: bool = True
    inspector_visible: bool = True
    filmstrip_visible: bool = True
    left_width: int = 52
    secondary_width: int = 224
    inspector_width: int = 284

    def validate(self) -> None:
        if self.secondary_panel not in {"layers", "history", "images"}:
            raise InvalidDocument("workspace secondary panel is invalid")
        if min(self.left_width, self.secondary_width, self.inspector_width) < 40:
            raise InvalidDocument("workspace panel width is invalid")


def _default_arrangements() -> dict[str, WorkspaceArrangement]:
    return {
        "develop": WorkspaceArrangement(secondary_panel="images"),
        "edit": WorkspaceArrangement(secondary_panel="layers"),
        "retouch": WorkspaceArrangement(secondary_panel="history"),
        "review": WorkspaceArrangement(left_visible=False, secondary_panel="images"),
    }


@dataclass(slots=True)
class WorkspaceState:
    arrangement: str = "develop"
    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    active_tool: str = "select"
    selected_layer_id: str | None = None
    selected_mask_id: str | None = None
    before_after: str = "off"
    clipping_warnings: bool = False
    gamut_warning: bool = False
    soft_proof: bool = False
    canvas_tone: str = "medium"
    arrangements: dict[str, WorkspaceArrangement] = field(default_factory=_default_arrangements)

    def validate(self) -> None:
        if self.arrangement not in ARRANGEMENTS or not 0.01 <= self.zoom <= 64.0:
            raise InvalidDocument("workspace arrangement or zoom is invalid")
        if self.before_after not in {"off", "split", "side-by-side"}:
            raise InvalidDocument("before/after mode is invalid")
        if self.canvas_tone not in {"white", "light", "medium", "dark", "black"}:
            raise InvalidDocument("canvas surround is invalid")
        if set(self.arrangements) != ARRANGEMENTS:
            raise InvalidDocument("workspace arrangements are incomplete")
        for arrangement in self.arrangements.values():
            arrangement.validate()


@dataclass(slots=True)
class ExportPreset:
    id: str = "web-jpeg"
    name: str = "Web JPEG"
    format: str = "JPEG"
    scale: float = 1.0
    width: int | None = None
    height: int | None = None
    resolution: int = 96
    quality: int = 90
    color_space: str = "sRGB"
    bit_depth: int = 8
    metadata: str = "copyright"
    sharpening: str = "screen-standard"

    def validate(self) -> None:
        if self.format not in {"JPEG", "PNG", "TIFF", "WEBP", "PDF"}:
            raise InvalidDocument("export format is unsupported by the installed pipeline")
        if not 0.01 <= self.scale <= 16.0 or not 1 <= self.quality <= 100:
            raise InvalidDocument("export scale or quality is invalid")
        if self.metadata not in {"all", "copyright", "none"} or self.bit_depth not in {8, 16}:
            raise InvalidDocument("export metadata or bit depth is invalid")


@dataclass(slots=True)
class Document:
    id: str
    name: str
    source: Source
    created_at: float = field(default_factory=time.time)
    modified_at: float = field(default_factory=time.time)
    working_space: str = "sRGB"
    rendering_intent: str = "relative-colorimetric"
    raw_development: list[Adjustment] = field(default_factory=list)
    crop: Crop = field(default_factory=Crop)
    layers: list[Layer] = field(default_factory=list)
    retouch: list[RetouchOperation] = field(default_factory=list)
    history: list[HistoryEntry] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    export_presets: list[ExportPreset] = field(default_factory=lambda: [ExportPreset()])
    metadata_edits: dict[str, str | int | float | list[str] | None] = field(default_factory=dict)
    workspace: WorkspaceState = field(default_factory=WorkspaceState)

    @classmethod
    def new(cls, uri: str, **metadata: Any) -> "Document":
        source = Source.from_uri(uri, **metadata)
        base = Layer(new_id("layer"), source.display_name, "raw" if source.raw else "pixel", source_uri=uri)
        document = cls(new_id("document"), Path(source.display_name).stem or "Untitled", source, layers=[base])
        document.workspace.selected_layer_id = base.id
        document.history.append(HistoryEntry(new_id("history"), "Open image"))
        return document

    def touch(self) -> None:
        self.modified_at = time.time()

    def layer(self, layer_id: str | None) -> Layer | None:
        def walk(items: list[Layer]) -> Layer | None:
            for item in items:
                if item.id == layer_id:
                    return item
                found = walk(item.children)
                if found:
                    return found
            return None
        return walk(self.layers)

    def mask(self, mask_id: str | None) -> Mask | None:
        def walk(items: list[Mask]) -> Mask | None:
            for item in items:
                if item.id == mask_id:
                    return item
                found = walk(item.children)
                if found:
                    return found
            return None
        for layer in self.walk_layers():
            found = walk(layer.masks)
            if found:
                return found
        return None

    def walk_layers(self) -> Iterable[Layer]:
        def walk(items: list[Layer]) -> Iterable[Layer]:
            for item in items:
                yield item
                yield from walk(item.children)
        return walk(self.layers)

    def recipe_dict(self) -> dict[str, Any]:
        return {
            "working_space": self.working_space,
            "rendering_intent": self.rendering_intent,
            "raw_development": [asdict(item) for item in self.raw_development],
            "crop": asdict(self.crop),
            "layers": [asdict(item) for item in self.layers],
            "retouch": [asdict(item) for item in self.retouch],
            "metadata_edits": copy.deepcopy(self.metadata_edits),
        }

    def validate(self) -> None:
        if not self.id or not self.name.strip():
            raise InvalidDocument("document requires id and name")
        self.source.validate()
        self.crop.validate()
        ids: set[str] = {self.id}
        for layer in self.walk_layers():
            layer.validate()
            if layer.id in ids:
                raise InvalidDocument(f"duplicate document id: {layer.id}")
            ids.add(layer.id)
            for mask in layer.masks:
                if mask.id in ids:
                    raise InvalidDocument(f"duplicate document id: {mask.id}")
                ids.add(mask.id)
        for adjustment in self.raw_development:
            adjustment.validate()
            if adjustment.id in ids:
                raise InvalidDocument(f"duplicate document id: {adjustment.id}")
            ids.add(adjustment.id)
        for operation in self.retouch:
            operation.validate()
            if operation.id in ids:
                raise InvalidDocument(f"duplicate document id: {operation.id}")
            ids.add(operation.id)
        for preset in self.export_presets:
            preset.validate()
        self.workspace.validate()
        if self.workspace.selected_layer_id and not self.layer(self.workspace.selected_layer_id):
            raise InvalidDocument("workspace selects a missing layer")
        if self.workspace.selected_mask_id and not self.mask(self.workspace.selected_mask_id):
            raise InvalidDocument("workspace selects a missing mask")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"format": "org.projectluma.darkroom-document", "schema_version": CURRENT_SCHEMA_VERSION, "document": asdict(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Document":
        payload = migrate_payload(copy.deepcopy(payload))
        if payload.get("format") != "org.projectluma.darkroom-document":
            raise InvalidDocument("not a Darkroom document")
        raw = payload.get("document")
        if not isinstance(raw, dict):
            raise InvalidDocument("document body is missing")

        def parse_mask(value: dict[str, Any]) -> Mask:
            value = dict(value)
            value["children"] = [parse_mask(item) for item in value.get("children", [])]
            return Mask(**value)

        def parse_layer(value: dict[str, Any]) -> Layer:
            value = dict(value)
            value["adjustments"] = [Adjustment(**item) for item in value.get("adjustments", [])]
            value["masks"] = [parse_mask(item) for item in value.get("masks", [])]
            value["children"] = [parse_layer(item) for item in value.get("children", [])]
            value["transform"] = Transform(**value.get("transform", {}))
            return Layer(**value)

        try:
            raw["source"] = Source(**raw["source"])
            raw["raw_development"] = [Adjustment(**item) for item in raw.get("raw_development", [])]
            raw["crop"] = Crop(**raw.get("crop", {}))
            raw["layers"] = [parse_layer(item) for item in raw.get("layers", [])]
            raw["retouch"] = [RetouchOperation(**item) for item in raw.get("retouch", [])]
            raw["history"] = [HistoryEntry(**item) for item in raw.get("history", [])]
            raw["snapshots"] = [Snapshot(**item) for item in raw.get("snapshots", [])]
            raw["export_presets"] = [ExportPreset(**item) for item in raw.get("export_presets", [])]
            workspace = dict(raw.get("workspace", {}))
            workspace["arrangements"] = {key: WorkspaceArrangement(**value) for key, value in workspace.get("arrangements", _default_arrangements()).items()}
            raw["workspace"] = WorkspaceState(**workspace)
            document = cls(**raw)
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidDocument(f"malformed Darkroom document: {error}") from error
        document.validate()
        return document

    def clone(self) -> "Document":
        return Document.from_dict(self.to_dict())


def migrate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("schema_version")
    if not isinstance(version, int) or version < 0:
        raise InvalidDocument("schema_version is missing or invalid")
    if version > CURRENT_SCHEMA_VERSION:
        raise UnsupportedDocumentVersion(version)
    while version < CURRENT_SCHEMA_VERSION:
        if version == 0:
            raw = payload.setdefault("document", {})
            raw.setdefault("rendering_intent", "relative-colorimetric")
            raw.setdefault("retouch", [])
            raw.setdefault("snapshots", [])
            raw.setdefault("export_presets", [asdict(ExportPreset())])
            raw.setdefault("metadata_edits", {})
            raw.setdefault("workspace", asdict(WorkspaceState()))
            payload["schema_version"] = version = 1
        else:  # pragma: no cover
            raise UnsupportedDocumentVersion(version)
    return payload


class DocumentStore:
    """Atomic documents plus explicit autosave recovery records."""

    def __init__(self, recovery_root: Path) -> None:
        self.recovery_root = recovery_root

    @staticmethod
    def load(path: Path) -> Document:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise InvalidDocument(f"document JSON is damaged: {error}") from error
        except OSError as error:
            raise DocumentError(f"could not read {path}: {error.strerror or error}") from error
        if not isinstance(payload, dict):
            raise InvalidDocument("document root must be an object")
        return Document.from_dict(payload)

    @staticmethod
    def _atomic_write(payload: dict[str, Any], path: Path) -> None:
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    @classmethod
    def save(cls, document: Document, path: Path) -> None:
        document.touch()
        cls._atomic_write(document.to_dict(), path)

    def recovery_path(self, document: Document) -> Path:
        return self.recovery_root / f"{document.id}.autosave{DOCUMENT_SUFFIX}"

    def autosave(self, document: Document, original_path: Path | None) -> Path:
        payload = document.to_dict()
        payload["recovery"] = {"original_path": str(original_path) if original_path else None, "saved_at": time.time()}
        path = self.recovery_path(document)
        self._atomic_write(payload, path)
        return path

    def discard_recovery(self, document_id: str) -> None:
        (self.recovery_root / f"{document_id}.autosave{DOCUMENT_SUFFIX}").unlink(missing_ok=True)

    def recoverable(self) -> Iterable[tuple[Path, Document, dict[str, Any]]]:
        if not self.recovery_root.exists():
            return []
        found: list[tuple[Path, Document, dict[str, Any]]] = []
        for path in sorted(self.recovery_root.glob(f"*.autosave{DOCUMENT_SUFFIX}")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                metadata = payload.get("recovery", {})
                document = Document.from_dict(payload)
            except (OSError, ValueError, DocumentError):
                continue
            original = metadata.get("original_path")
            if original:
                try:
                    if Path(original).stat().st_mtime >= float(metadata.get("saved_at", 0)):
                        continue
                except OSError:
                    pass
            found.append((path, document, metadata))
        return found
