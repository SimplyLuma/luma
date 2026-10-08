# SPDX-License-Identifier: Apache-2.0
"""Hardware updates in plain words, and the signed Luma firmware block list."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import minisign_signer as signer
from luma_installer import depot_firmware as fw

GUID = "230c8b18-8d9b-53ec-838b-6cfc0383493a"


class PlainWords(unittest.TestCase):
    def test_dbx_is_a_security_update_for_startup_protection(self):
        d = fw.describe("UEFI dbx", protocols=("org.uefi.dbx",), plugin="uefi_dbx")
        self.assertEqual(d.title, "Security update for your computer’s startup protection")
        self.assertTrue(d.security)

    def test_modem(self):
        d = fw.describe("Fibocom L850-GL", icons=("modem",), plugin="modem_manager")
        self.assertEqual(d.title, "Update for your mobile broadband modem")
        self.assertFalse(d.security)

    def test_system_firmware_and_urgency(self):
        d = fw.describe("System Firmware", protocols=("org.uefi.capsule",), urgency="critical")
        self.assertEqual(d.title, "Update for your computer’s firmware")
        self.assertTrue(d.security)

    def test_unknown_devices_still_get_a_sentence_and_no_raw_versions(self):
        d = fw.describe("Frobnicator 3000")
        self.assertEqual(d.title, "Update for Frobnicator 3000")
        self.assertTrue(d.summary)
        self.assertNotRegex(d.title + d.summary, r"\d+\.\d+")

    def test_needs(self):
        self.assertEqual(fw.needs_text(True, True), "Needs power connected · Finishes when you restart")
        self.assertEqual(fw.needs_text(False, False), "")


class BlockList(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.key = signer.public_key_file()

    def document(self, generated="2026-09-17T00:00:00Z", entries=()):
        return json.dumps({"schema": fw.SCHEMA, "generated_at": generated, "entries": list(entries)}).encode()

    def write(self, name, content, signature=None):
        path = Path(self.dir.name) / name
        path.write_bytes(content)
        Path(str(path) + ".minisig").write_bytes(signature if signature is not None else signer.signature_file(content))
        return path

    def test_a_signed_list_blocks_a_version_or_every_release(self):
        content = self.document(entries=[{"guid": GUID.upper(), "version": "1.2.3", "reason": "bricks model X"},
                                         {"guid": "11111111-2222-3333-4444-555555555555"}])
        blocklist = fw.parse(content, signer.signature_file(content), self.key)
        self.assertEqual(blocklist.blocks([GUID], "1.2.3").reason, "bricks model X")
        self.assertIsNone(blocklist.blocks([GUID], "1.2.4"))
        self.assertIsNotNone(blocklist.blocks(["11111111-2222-3333-4444-555555555555"], "9"))

    def test_unsigned_or_altered_lists_are_refused(self):
        content = self.document(entries=[{"guid": GUID}])
        with self.assertRaises(fw.BlockListError):
            fw.parse(content + b" ", signer.signature_file(content), self.key)
        shipped = self.write("shipped.json", content, signature=b"not a signature")
        self.assertEqual(fw.current(self.key, shipped=shipped, cached=Path(self.dir.name) / "none.json"), fw.EMPTY)

    def test_no_list_blocks_nothing_and_the_newest_verified_list_wins(self):
        self.assertEqual(fw.current(self.key, shipped=Path(self.dir.name) / "a", cached=Path(self.dir.name) / "b"),
                         fw.EMPTY)
        shipped = self.write("shipped.json", self.document("2026-09-17T00:00:00Z"))
        cached = Path(self.dir.name) / "cache" / "firmware-blocklist.json"
        newer = self.document("2026-09-20T00:00:00Z", [{"guid": GUID}])
        fw.remember(newer, signer.signature_file(newer), self.key, cached=cached)
        self.assertEqual(len(fw.current(self.key, shipped=shipped, cached=cached).entries), 1)
        older = self.document("2026-09-18T00:00:00Z")
        with self.assertRaises(fw.BlockListError):
            fw.remember(older, signer.signature_file(older), self.key, cached=cached)


if __name__ == "__main__":
    unittest.main()
