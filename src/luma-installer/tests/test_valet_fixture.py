"""The conformance fixture is isolated from Valet's real stores."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE = Path(__file__).resolve().parents[1] / "luma_installer" / "valet_fixture.py"
FIXTURE = next(
    candidate for candidate in (
        Path(__file__).resolve().parents[0] / "fixtures/valet-v70.json",
        ROOT / "tests/fixtures/valet-v70.json",
    ) if candidate.is_file()
)
spec = importlib.util.spec_from_file_location("valet_fixture", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ValetFixtureTests(unittest.TestCase):
    def test_sample_and_in_memory_choices(self):
        fixture = module.ValetFixture(FIXTURE)
        self.assertEqual((len(fixture.apps), len(fixture.installed)), (6, 2))
        self.assertEqual((fixture.view, fixture.app_id), ("install", "kiln"))
        self.assertTrue(fixture.permission_allowed(0))
        self.assertFalse(fixture.toggle_permission(0))
        fixture.open_app("driftwood")
        self.assertTrue(fixture.permission_allowed(0))
        fixture.open_app("kiln")
        self.assertFalse(fixture.permission_allowed(0))
        fixture.open_removal("harbor")
        self.assertEqual(fixture.removed_bytes_mb(), 1172)
        fixture.keep = False
        self.assertEqual(fixture.removed_bytes_mb(), 1214)

    def test_unsupported_permission_does_not_change_state(self):
        fixture = module.ValetFixture(FIXTURE)
        fixture.open_app("labelpress")
        with self.assertRaises(ValueError):
            fixture.toggle_permission(0)
        self.assertEqual(fixture.permissions, {})


if __name__ == "__main__":
    unittest.main()
