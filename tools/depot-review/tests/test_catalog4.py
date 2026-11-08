# SPDX-License-Identifier: Apache-2.0
import fcntl
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depot_review import catalog4  # noqa: E402
from depot_review.config import Config  # noqa: E402


class Catalog4(unittest.TestCase):
    def test_runs_the_repository_signer_against_the_hub(self):
        config = Config(source=Path("/src"), depot_root=Path("/root"), depot_keys=Path("/keys"))
        argv = catalog4.command(config)
        self.assertEqual(argv[:3], ["python3", "-I", "/src/scripts/depot/sign-catalog.py"])
        self.assertIn("--site", argv)
        self.assertEqual(argv[argv.index("--site") + 1], "/root/site")
        self.assertIn("sync-remote.sh' --media && bash", argv[-1])
        config.sync_remote = False
        self.assertNotIn("--upload", catalog4.command(config))

    def test_a_held_publication_lock_skips_the_round(self):
        with tempfile.NamedTemporaryFile() as lock:
            config = Config(publication_locks=[lock.name])
            with open(lock.name, "a") as other:
                fcntl.flock(other, fcntl.LOCK_EX)
                self.assertIsNone(catalog4.publish(config))


if __name__ == "__main__":
    unittest.main()
