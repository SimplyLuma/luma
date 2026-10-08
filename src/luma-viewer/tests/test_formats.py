# SPDX-License-Identifier: Apache-2.0
import bz2
import gzip
import lzma
import tempfile
import unittest
from pathlib import Path

from luma_viewer import formats

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class CompressedPdfTests(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.root = Path(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def test_plain_and_compressed_pdfs_are_tier_one_pdfs(self):
        writers = {".pdf": lambda d: d, ".pdf.gz": gzip.compress,
                   ".pdf.bz2": bz2.compress, ".PDF.XZ": lzma.compress}
        for suffix, compress in writers.items():
            path = self.root / f"report{suffix}"
            path.write_bytes(compress(PDF))
            facts = formats.inspect(path)
            self.assertEqual((facts.tier, facts.kind), (formats.TIER_ONE, "PDF document"), suffix)
            self.assertEqual(formats.read_pdf_bytes(path), PDF, suffix)

    def test_other_compressed_files_stay_archives(self):
        path = self.root / "notes.txt.gz"
        path.write_bytes(gzip.compress(b"hello"))
        self.assertIsNone(formats.pdf_compression(path))
        self.assertEqual(formats.inspect(path).kind, "Archive")
        self.assertIsNone(formats.pdf_compression(self.root / "backup.gz"))

    def test_inflation_past_the_session_limit_is_refused(self):
        path = self.root / "bomb.pdf.gz"
        path.write_bytes(gzip.compress(b"\0" * 4096))
        with self.assertRaisesRegex(ValueError, "session limit"):
            formats.read_pdf_bytes(path, limit=1024)

    def test_damaged_archive_is_reported_not_raised_raw(self):
        path = self.root / "broken.pdf.xz"
        path.write_bytes(b"not xz at all")
        with self.assertRaisesRegex(ValueError, "damaged"):
            formats.read_pdf_bytes(path)


class DesktopEntryTests(unittest.TestCase):
    def test_every_pdf_type_is_declared(self):
        entry = (Path(__file__).resolve().parents[1] / "data" / "org.projectluma.Viewer.desktop").read_text()
        declared = next(line for line in entry.splitlines() if line.startswith("MimeType="))
        types = set(declared.split("=", 1)[1].strip(";").split(";"))
        for mime in ("application/pdf", "application/x-bzpdf", "application/x-gzpdf",
                     "application/x-xzpdf", "application/x-ext-pdf"):
            self.assertIn(mime, types)

class AudioPreviewTests(unittest.TestCase):
    def test_audio_is_previewable_and_keeps_its_music_owner(self):
        for suffix in (".mp3", ".flac", ".wav", ".ogg", ".m4a"):
            facts = formats.inspect(Path("track" + suffix))
            self.assertEqual((facts.kind, facts.tier, facts.owner), ("Audio", formats.TIER_TWO, "Tide"))
            self.assertNotIn("does not play", facts.owner_note)


if __name__ == "__main__":
    unittest.main()
