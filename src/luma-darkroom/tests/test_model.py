# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luma_darkroom.editing import EditError, Editor
from luma_darkroom.model import Crop, Document, DocumentStore, InvalidDocument, UnsupportedDocumentVersion


class DocumentModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.png"
        self.source.write_bytes(b"identity fixture")
        self.document = Document.new(self.source.as_uri(), width=800, height=600)
        self.editor = Editor(self.document)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trip_keeps_non_destructive_recipe(self) -> None:
        self.editor.set_adjustment("exposure", 0.75)
        layer = self.editor.add_layer("adjustment", "Warm portrait")
        self.editor.set_adjustment("temperature", 18, layer_id=layer.id)
        mask = self.editor.add_mask(layer.id, "radial-gradient", geometry={"x": 0.5, "y": 0.4, "radius": 0.3})
        self.editor.set_crop(Crop(left=0.1, top=0.1, right=0.9, bottom=0.95), name="Crop to portrait")
        loaded = Document.from_dict(self.document.to_dict())
        self.assertEqual(loaded.source.uri, self.source.as_uri())
        self.assertEqual(loaded.raw_development[0].value, 0.75)
        self.assertEqual(loaded.layer(layer.id).masks[0].id, mask.id)
        self.assertEqual(loaded.crop.left, 0.1)

    def test_source_disappearance_is_recoverable_state(self) -> None:
        self.source.unlink()
        loaded = Document.from_dict(self.document.to_dict())
        self.assertTrue(loaded.source.missing)
        self.assertEqual(len(loaded.layers), 1)

    def test_atomic_save_and_recovery(self) -> None:
        destination = self.root / "portrait.luma-darkroom"
        DocumentStore.save(self.document, destination)
        self.assertEqual(DocumentStore.load(destination).id, self.document.id)
        self.assertEqual(list(self.root.glob(f".{destination.name}.*.tmp")), [])
        store = DocumentStore(self.root / "recovery")
        recovery = store.autosave(self.document, None)
        self.assertEqual(len(list(store.recoverable())), 1)
        store.discard_recovery(self.document.id)
        self.assertFalse(recovery.exists())

    def test_newer_documents_are_rejected(self) -> None:
        payload = self.document.to_dict()
        payload["schema_version"] = 99
        with self.assertRaises(UnsupportedDocumentVersion):
            Document.from_dict(payload)

    def test_crop_validation_preserves_original(self) -> None:
        before = self.document.to_dict()
        with self.assertRaises(InvalidDocument):
            self.editor.set_crop(Crop(left=0.8, right=0.2))
        self.assertEqual(self.document.to_dict(), before)

    def test_continuous_adjustment_is_one_undo_step(self) -> None:
        self.editor.set_adjustment("exposure", 0.1, coalesce=True)
        self.editor.set_adjustment("exposure", 0.2, coalesce=True)
        self.editor.set_adjustment("exposure", 0.3, coalesce=True)
        self.editor.end_continuous_edit()
        self.assertEqual(self.document.raw_development[0].value, 0.3)
        self.editor.undo()
        self.assertEqual(self.document.raw_development, [])
        self.editor.redo()
        self.assertEqual(self.document.raw_development[0].value, 0.3)

    def test_layer_reorder_mask_and_snapshot(self) -> None:
        first = self.editor.add_layer("adjustment", "Curves")
        second = self.editor.add_layer("fill", "Wash")
        self.editor.reorder_layer(second.id, 1)
        self.assertEqual(self.document.layers[1].id, second.id)
        mask = self.editor.add_mask(first.id, "brush")
        self.editor.toggle_mask(mask.id)
        self.assertFalse(mask.enabled)
        snapshot = self.editor.create_snapshot("Soft proof")
        self.assertEqual(snapshot.name, "Soft proof")

    def test_base_layer_cannot_be_deleted(self) -> None:
        with self.assertRaises(EditError):
            self.editor.remove_layer(self.document.layers[0].id)

    def test_selected_non_base_layer_can_be_deleted_and_undone(self) -> None:
        layer = self.editor.add_layer("adjustment", "Temporary")
        self.editor.remove_layer(layer.id)
        self.assertIsNone(self.document.layer(layer.id))
        self.assertIsNotNone(self.document.workspace.selected_layer_id)
        self.editor.undo()
        self.assertIsNotNone(self.document.layer(layer.id))


if __name__ == "__main__":
    unittest.main()
