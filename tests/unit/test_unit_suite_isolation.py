# SPDX-License-Identifier: Apache-2.0

"""Keep one test module from deciding what the rest of the suite imports.

`tests/unit/test_fp6_cellular_link.py` used to install a stub `gi` into
`sys.modules` at module scope and never take it out again.  `unittest`
discovers alphabetically, so everything later in the suite that called
`gi.require_version` got the stub and failed with "module 'gi' has no attribute
'require_version'".  It cost 14 errors and 30 unrun tests, and it was invisible
because each module passed when it was run on its own.

A stub belongs in `setUpModule`, paired with a `tearDownModule` that puts back
whatever was there before.  This module fails if a test module goes back to
writing `sys.modules` where import alone is enough to change the suite.

It also fails on a skip with no stated reason.  A skipped test still prints
OK, so a suite that skips everything is indistinguishable from one that
checked everything: `test_luma_menu_contract` reported "OK (skipped=10)" for
weeks while proving nothing.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


UNIT_TESTS = Path(__file__).resolve().parent

# Every entry needs a reason. Silence is how something ends up ungated by
# accident.
ALLOWED: dict[str, str] = {}


def module_scope_sys_modules_writes(source: str) -> list[str]:
    """Return a description of each top-level write to `sys.modules`.

    Only module scope counts: the same statement inside `setUpModule`, a
    fixture or a test method is scoped and is what we want people to write.
    """

    findings: list[str] = []

    def is_sys_modules(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "modules"
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        )

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
            ):
                continue  # scoped: runs when called, not when imported
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Subscript) and is_sys_modules(target.value):
                        findings.append(f"line {child.lineno}: sys.modules[...] = ...")
            if isinstance(child, ast.Call):
                function = child.func
                if (
                    isinstance(function, ast.Attribute)
                    and function.attr in {"setdefault", "update", "pop", "__setitem__"}
                    and is_sys_modules(function.value)
                ):
                    findings.append(
                        f"line {child.lineno}: sys.modules.{function.attr}(...)"
                    )
            walk(child)

    walk(ast.parse(source))
    return findings


CONTAMINATING_MODULE = """
import sys, types
GI = types.ModuleType("gi")
sys.modules.setdefault("gi", GI)
sys.modules["gi.repository"] = GI

class Tests:
    def test_scoped(self):
        sys.modules["gi"] = GI
"""

CLEAN_MODULE = """
import sys, types

def setUpModule():
    sys.modules["gi"] = types.ModuleType("gi")

def tearDownModule():
    sys.modules.pop("gi", None)
"""


SKIP_CALLS = {"skip", "skipIf", "skipUnless", "skipTest", "SkipTest"}


def unexplained_skips(source: str) -> list[str]:
    """Skips with no reason a reader could act on.

    A suite that skips everything prints OK and proves nothing, which is the
    same silent success as a check that never runs. A skip is allowed, but it
    has to say what is missing.
    """

    findings: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.attr if isinstance(function, ast.Attribute)
            else function.id if isinstance(function, ast.Name)
            else None
        )
        if name not in SKIP_CALLS:
            continue
        reason = node.args[-1] if node.args else None
        if name in {"skipIf", "skipUnless"}:
            reason = node.args[1] if len(node.args) > 1 else None
        if reason is None:
            findings.append(f"line {node.lineno}: {name}() with no reason")
        elif isinstance(reason, ast.Constant) and not str(reason.value).strip():
            findings.append(f"line {node.lineno}: {name}() with an empty reason")
    return findings


UNEXPLAINED_SKIP = """
import unittest

class Tests(unittest.TestCase):
    @unittest.skipIf(True)
    def test_one(self):
        pass

    def test_two(self):
        raise unittest.SkipTest("")

    def test_three(self):
        self.skipTest("")
"""

EXPLAINED_SKIP = """
import unittest

class Tests(unittest.TestCase):
    @unittest.skipUnless(HAVE_GTK, "gtk4 is not installed here")
    def test_one(self):
        pass

    def test_two(self):
        raise unittest.SkipTest(f"no display ({reason})")
"""


class SkipsSayWhy(unittest.TestCase):
    def test_the_detector_catches_a_silent_skip(self):
        findings = unexplained_skips(UNEXPLAINED_SKIP)
        self.assertEqual(len(findings), 3, findings)

    def test_the_detector_accepts_a_stated_reason(self):
        self.assertEqual(unexplained_skips(EXPLAINED_SKIP), [])

    def test_no_unit_test_skips_without_saying_why(self):
        offenders = {}
        for path in sorted(UNIT_TESTS.glob("test_*.py")):
            findings = unexplained_skips(path.read_text(encoding="utf-8"))
            if findings:
                offenders[path.name] = findings
        self.assertEqual(
            offenders,
            {},
            "A skipped test still prints OK. Say what is missing, so a green "
            "run can be told from a run that checked nothing.",
        )


class SysModulesIsolationTests(unittest.TestCase):
    def test_the_detector_catches_the_bug_it_was_written_for(self):
        findings = module_scope_sys_modules_writes(CONTAMINATING_MODULE)
        self.assertEqual(
            len(findings), 2, f"expected both module-scope writes, got {findings}"
        )
        self.assertTrue(any("setdefault" in item for item in findings), findings)

    def test_the_detector_accepts_a_scoped_stub(self):
        self.assertEqual(module_scope_sys_modules_writes(CLEAN_MODULE), [])

    def test_no_unit_test_module_stubs_sys_modules_on_import(self):
        offenders = {}
        for path in sorted(UNIT_TESTS.glob("test_*.py")):
            if path.name in ALLOWED:
                continue
            findings = module_scope_sys_modules_writes(
                path.read_text(encoding="utf-8")
            )
            if findings:
                offenders[path.name] = findings
        self.assertEqual(
            offenders,
            {},
            "These modules change sys.modules just by being imported, so they "
            "change what every later test in the suite imports. Move the stub "
            "into setUpModule and restore it in tearDownModule.",
        )

    def test_the_allowlist_gives_a_reason_for_every_entry(self):
        for name, reason in ALLOWED.items():
            self.assertTrue(reason.strip(), f"{name} is excluded with no reason")


if __name__ == "__main__":
    unittest.main()
