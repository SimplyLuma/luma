#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a unittest suite in %check and fail unless enough tests really ran.

    counted-unittest.py START_DIR FLOOR
    counted-unittest.py --self-test

`unittest discover` exits 0 when it finds nothing, so a tarball that lost its
tests looks exactly like a passing build. This refuses a suite that discovers
fewer than FLOOR tests, or that ran fewer than FLOOR once skips are removed,
and fails on any failure or error (an import error is one).
"""
import os
import pathlib
import sys
import tempfile
import unittest


def run(start: str, floor: int, verbosity: int = 2) -> int:
    say = print if verbosity else (lambda *a, **k: None)
    suite = unittest.defaultTestLoader.discover(start, pattern="test_*.py")
    found = suite.countTestCases()
    say(f"{start}: {found} unit tests discovered (floor {floor})", flush=True)
    if found < floor:
        say(f"error: only {found} unit tests discovered under {start}; expected at least {floor}",
              file=sys.stderr)
        return 1
    stream = sys.stderr if verbosity else open(os.devnull, "w")
    result = unittest.TextTestRunner(stream=stream, verbosity=verbosity).run(suite)
    skipped = len(result.skipped)
    ran = result.testsRun - skipped
    say(f"{start}: ran {result.testsRun}, skipped {skipped}, "
          f"failures {len(result.failures)}, errors {len(result.errors)}", flush=True)
    if not result.wasSuccessful():
        return 1
    if ran < floor:
        say(f"error: only {ran} unit tests ran unskipped under {start}; expected at least {floor}",
              file=sys.stderr)
        return 1
    return 0


def self_test() -> int:
    cases = {
        "empty directory": ({}, 1, 1),
        "below the floor": ({"test_a.py": "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n"}, 2, 1),
        "failing test": ({"test_a.py": "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): self.fail('x')\n"}, 1, 1),
        "import error": ({"test_a.py": "import no_such_module_for_luma_self_test\n"}, 1, 1),
        "all skipped": ({"test_a.py": "import unittest\nclass T(unittest.TestCase):\n    @unittest.skip('x')\n    def test_a(self): pass\n"}, 1, 1),
        "passing suite": ({"test_a.py": "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n"}, 1, 0),
    }
    wrong = []
    for name, (files, floor, expected) in cases.items():
        with tempfile.TemporaryDirectory() as directory:
            for leaf, text in files.items():
                (pathlib.Path(directory) / leaf).write_text(text)
            sys.modules.pop("test_a", None)
            got = run(directory, floor, verbosity=0)
        if got != expected:
            wrong.append(f"{name}: exit {got}, expected {expected}")
    for line in wrong:
        print(f"self-test failed: {line}", file=sys.stderr)
    print(f"counted-unittest self-test: {len(cases) - len(wrong)}/{len(cases)} cases behaved")
    return 1 if wrong else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(self_test())
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(run(sys.argv[1], int(sys.argv[2])))
