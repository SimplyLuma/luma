# SPDX-License-Identifier: Apache-2.0
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from luma_leaf import data_paths

class DataPaths(unittest.TestCase):
    def test_explicit_sandbox_assets_and_missing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / "app" / "share" / "leaf"
            assets.mkdir(parents=True)
            with patch.dict(os.environ, {"LUMA_LEAF_DATA_DIR": str(assets)}):
                self.assertEqual(data_paths.data_directory(), assets)
                assets.rmdir()
                with self.assertRaises(FileNotFoundError):
                    data_paths.data_directory()

    def test_package_prefix_without_source_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True), patch.object(data_paths, "__file__", str(Path(directory)/"lib/luma_leaf/data_paths.py")), patch.object(data_paths.sys, "prefix", "/app"):
                self.assertEqual(data_paths.data_directory(), Path("/app/share/leaf"))

if __name__ == "__main__": unittest.main()
