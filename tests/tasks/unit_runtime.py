# SPDX-License-Identifier: Apache-2.0
"""Run every Tasks unit against a disposable staged Python/fixture payload."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

assert os.environ.get("LUMA_TASKS_ISOLATED_TEST") == "1"
temporary = Path(os.environ["XDG_DATA_HOME"]).parent
assert temporary.name.startswith("luma-tasks-test-")
assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=" + str(temporary / "bus")
root = Path(__file__).resolve().parents[2]

with tempfile.TemporaryDirectory(prefix="unit-stage-", dir=temporary) as staged:
    stage = Path(staged)
    site = stage / "site"
    # These are the %install Python payloads, with the source tests exported
    # beside the fixture. No source tree is present to shadow the staged app.
    for package in ("prairie_apps", "prairie_ui"):
        target = site / package
        target.mkdir(parents=True)
        for source in (root / "src/prairie-core" / package).glob("*.py"):
            shutil.copyfile(source, target / source.name)
    tests = stage / "tests/unit"
    tests.mkdir(parents=True)
    for source in (root / "tests/unit").glob("test_prairie_tasks*.py"):
        shutil.copyfile(source, tests / source.name)
    fixture = stage / "tests/fixtures/tasks-v70.json"
    fixture.parent.mkdir()
    original = root / "tests/fixtures/tasks-v70.json"
    shutil.copyfile(original, fixture)
    subprocess.run([sys.executable, "-c", """
import os
import unittest
from prairie_apps import tasks_backend
print("Staged Tasks backend:", tasks_backend.__file__, flush=True)
assert tasks_backend.__file__.startswith(os.environ["PYTHONPATH"] + "/")
tasks_backend._modules()  # Real EDataServer/ECal/ICalGLib; never accept skips.
suite = unittest.defaultTestLoader.discover(
    os.environ["PYTHONPATH"] + "/../tests/unit", pattern="test_prairie_tasks*.py")
assert suite.countTestCases() == 52, suite.countTestCases()
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert not result.skipped, result.skipped
raise SystemExit(not result.wasSuccessful())
"""], env=dict(os.environ, PYTHONPATH=str(site), PYTHONDONTWRITEBYTECODE="1",
             DISPLAY="", WAYLAND_DISPLAY=""), check=True)
    assert fixture.read_bytes() == original.read_bytes(), "Unit tests changed the fixture"
