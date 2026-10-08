# SPDX-License-Identifier: Apache-2.0
"""Undoable recipe operations for Darkroom."""

from __future__ import annotations

import copy
import time
from dataclasses import asdict
from typing import Callable

from .model import (
    Adjustment,
    BLEND_MODES,
    Crop,
    Document,
    InvalidDocument,
    Layer,
    Mask,
    RetouchOperation,
    Snapshot,
    new_id,
)


class EditError(RuntimeError):
    pass


class Editor:
    """Mutates document recipes while keeping bounded undo/redo snapshots.

    A drag can repeatedly pass the same ``coalesce`` key. It becomes one human
    history operation even though every preview update is live.
    """

    def __init__(self, document: Document, history_limit: int = 120) -> None:
        self.document = document
        self.history_limit = history_limit
        self._undo: list[tuple[str, dict]] = []
        self._redo: list[tuple[str, dict]] = []
        self._coalesce_key: str | None = None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _state(self) -> dict:
        return self.document.to_dict()

    def _restore(self, payload: dict) -> None:
        restored = Document.from_dict(payload)
        for field in restored.__dataclass_fields__:
            setattr(self.document, field, copy.deepcopy(getattr(restored, field)))

    def change(self, name: str, operation: Callable[[], None], coalesce: str | None = None) -> None:
        before = self._state()
        operation()
        self.document.validate()
        if before == self._state():
            return
        if coalesce is None or coalesce != self._coalesce_key:
            self._undo.append((name, before))
            del self._undo[:-self.history_limit]
            self.document.history.append(self._history(name))
            del self.document.history[:-self.history_limit]
        else:
            self._undo[-1] = (name, self._undo[-1][1])
            self.document.history[-1].name = name
        self._coalesce_key = coalesce
        self._redo.clear()
        self.document.touch()

    @staticmethod
    def _history(name: str):
        from .model import HistoryEntry
        return HistoryEntry(new_id("history"), name)

    def end_continuous_edit(self) -> None:
        self._coalesce_key = None

    def undo(self) -> str:
        self.end_continuous_edit()
        if not self._undo:
            raise EditError("Nothing to undo")
        name, state = self._undo.pop()
        self._redo.append((name, self._state()))
        self._restore(state)
        return name

    def redo(self) -> str:
        self.end_continuous_edit()
        if not self._redo:
            raise EditError("Nothing to redo")
        name, state = self._redo.pop()
        self._undo.append((name, self._state()))
        self._restore(state)
        return name

    def set_adjustment(self, kind: str, value, *, layer_id: str | None = None, coalesce: bool = False) -> Adjustment:
        collection = self.document.raw_development
        prefix = "Adjust"
        if layer_id:
            layer = self.document.layer(layer_id)
            if layer is None:
                raise EditError("The selected layer no longer exists")
            if layer.locked:
                raise EditError(f"{layer.name} is locked")
            collection = layer.adjustments
            prefix = f"Adjust {layer.name}"
        adjustment = next((item for item in collection if item.kind == kind), None)
        if adjustment is None:
            adjustment = Adjustment(new_id("adjustment"), kind, value)

        def apply() -> None:
            existing = next((item for item in collection if item.kind == kind), None)
            if existing is None:
                collection.append(adjustment)
            else:
                existing.value = copy.deepcopy(value)
                existing.enabled = True

        label = kind.replace("-", " ")
        self.change(f"{prefix} {label}", apply, f"adjust:{layer_id or 'develop'}:{kind}" if coalesce else None)
        return next(item for item in collection if item.kind == kind)

    def reset_adjustment(self, kind: str, *, layer_id: str | None = None) -> None:
        layer = self.document.layer(layer_id) if layer_id else None
        collection = layer.adjustments if layer else self.document.raw_development
        if not any(item.kind == kind for item in collection):
            return
        self.change(f"Reset {kind.replace('-', ' ')}", lambda: collection.__setitem__(slice(None), [item for item in collection if item.kind != kind]))

    def set_crop(self, crop: Crop, *, name: str = "Adjust crop", coalesce: bool = False) -> None:
        crop.validate()
        self.change(name, lambda: setattr(self.document, "crop", copy.deepcopy(crop)), "crop" if coalesce else None)

    def add_layer(self, kind: str, name: str, *, source_uri: str | None = None) -> Layer:
        layer = Layer(new_id("layer"), name, kind, source_uri=source_uri)
        self.change(f"Add {name}", lambda: self.document.layers.append(layer))
        self.document.workspace.selected_layer_id = layer.id
        return layer

    def duplicate_layer(self, layer_id: str) -> Layer:
        layer, parent, index = self._layer_location(layer_id)
        duplicate = copy.deepcopy(layer)

        def renew(item: Layer) -> None:
            item.id = new_id("layer")
            item.name = f"{item.name} copy"
            for mask in item.masks:
                self._renew_mask_ids(mask)
            for child in item.children:
                renew(child)

        renew(duplicate)
        self.change(f"Duplicate {layer.name}", lambda: parent.insert(index + 1, duplicate))
        self.document.workspace.selected_layer_id = duplicate.id
        return duplicate

    @staticmethod
    def _renew_mask_ids(mask: Mask) -> None:
        mask.id = new_id("mask")
        for child in mask.children:
            Editor._renew_mask_ids(child)

    def remove_layer(self, layer_id: str) -> None:
        layer, parent, index = self._layer_location(layer_id)
        if layer.source_uri == self.document.source.uri and layer is self.document.layers[0]:
            raise EditError("The base image layer cannot be deleted")

        def apply() -> None:
            parent.pop(index)
            self.document.workspace.selected_layer_id = self.document.layers[-1].id if self.document.layers else None
            self.document.workspace.selected_mask_id = None

        self.change(f"Delete {layer.name}", apply)

    def reorder_layer(self, layer_id: str, target_index: int, *, target_group_id: str | None = None) -> None:
        layer, source_parent, source_index = self._layer_location(layer_id)
        if layer.locked:
            raise EditError(f"{layer.name} is locked")
        target_parent = self.document.layers
        if target_group_id:
            group = self.document.layer(target_group_id)
            if group is None or group.kind != "group":
                raise EditError("The drop target is not a group")
            if group.id == layer.id or any(child.id == group.id for child in layer.children):
                raise EditError("A group cannot be placed inside itself")
            target_parent = group.children
        target_index = max(0, min(target_index, len(target_parent)))

        def apply() -> None:
            source_parent.pop(source_index)
            adjusted = target_index
            if source_parent is target_parent and source_index < target_index:
                adjusted -= 1
            target_parent.insert(adjusted, layer)

        destination = f" inside {self.document.layer(target_group_id).name}" if target_group_id else ""
        self.change(f"Move {layer.name}{destination}", apply)

    def set_layer_opacity(self, layer_id: str, opacity: float, *, coalesce: bool = False) -> None:
        layer = self._editable_layer(layer_id)
        value = max(0.0, min(1.0, opacity))
        self.change(f"Change {layer.name} opacity", lambda: setattr(layer, "opacity", value), f"opacity:{layer_id}" if coalesce else None)

    def set_blend_mode(self, layer_id: str, mode: str) -> None:
        layer = self._editable_layer(layer_id)
        if mode not in BLEND_MODES:
            raise EditError(f"Unsupported blend mode: {mode}")
        self.change(f"Change {layer.name} blend mode", lambda: setattr(layer, "blend_mode", mode))

    def toggle_layer(self, layer_id: str) -> None:
        layer = self.document.layer(layer_id)
        if layer is None:
            raise EditError("The selected layer no longer exists")
        self.change(("Show " if not layer.visible else "Hide ") + layer.name, lambda: setattr(layer, "visible", not layer.visible))

    def add_mask(self, layer_id: str, kind: str, *, name: str | None = None, operation: str = "add", geometry: dict | None = None) -> Mask:
        layer = self._editable_layer(layer_id)
        mask = Mask(new_id("mask"), name or kind.replace("-", " ").title(), kind, operation=operation, geometry=geometry or {})
        self.change(f"Add {mask.name.lower()}", lambda: layer.masks.append(mask))
        self.document.workspace.selected_mask_id = mask.id
        return mask

    def toggle_mask(self, mask_id: str) -> None:
        mask = self.document.mask(mask_id)
        if mask is None:
            raise EditError("The selected mask no longer exists")
        self.change(("Enable " if not mask.enabled else "Disable ") + mask.name, lambda: setattr(mask, "enabled", not mask.enabled))

    def add_retouch(self, kind: str, points: list[list[float]], **options) -> RetouchOperation:
        operation = RetouchOperation(new_id("retouch"), kind, points, **options)
        operation.validate()
        self.change({"heal": "Heal spot", "clone": "Clone area", "remove": "Remove object"}.get(kind, f"Apply {kind}"), lambda: self.document.retouch.append(operation))
        return operation

    def create_snapshot(self, name: str | None = None) -> Snapshot:
        snapshot = Snapshot(new_id("snapshot"), name or f"Snapshot {len(self.document.snapshots) + 1}", time.time(), self.document.recipe_dict())
        self.change(f"Create {snapshot.name}", lambda: self.document.snapshots.append(snapshot))
        return snapshot

    def relink_source(self, uri: str) -> None:
        from .model import Source
        replacement = Source.from_uri(uri)
        current = self.document.source
        replacement.content_type = current.content_type
        replacement.width = current.width
        replacement.height = current.height
        replacement.bit_depth = current.bit_depth
        replacement.embedded_profile = current.embedded_profile
        replacement.raw = current.raw

        def apply() -> None:
            self.document.source = replacement
            for layer in self.document.walk_layers():
                if layer.source_uri == current.uri:
                    layer.source_uri = uri
                    layer.missing_resource = False

        self.change("Relink original image", apply)

    def _editable_layer(self, layer_id: str) -> Layer:
        layer = self.document.layer(layer_id)
        if layer is None:
            raise EditError("The selected layer no longer exists")
        if layer.locked:
            raise EditError(f"{layer.name} is locked")
        return layer

    def _layer_location(self, layer_id: str) -> tuple[Layer, list[Layer], int]:
        def walk(items: list[Layer]):
            for index, item in enumerate(items):
                if item.id == layer_id:
                    return item, items, index
                found = walk(item.children)
                if found:
                    return found
            return None
        found = walk(self.document.layers)
        if found is None:
            raise EditError("The selected layer no longer exists")
        return found
