#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Find tests that pass where no build can see them.

A test that runs on a laptop and in nobody's %check gates nothing. It reports
success, it raises the test count, and it will not stop a release. That is
worse than having no test at all, because the absence of a test is obvious and
a test that is never run looks exactly like a test that is.

This happened for real: five rules about overriding a publisher's command line
were added to tests/unit/, passed every time they were run by hand, and gated
nothing, because luma-application-installer's %check discovers tests/ from the
package source tree and its build script copies src/luma-installer/tests into
it -- never the repository's own tests/unit. The build said "Ran 536 tests, OK"
both before and after they were written.

Reachability is judged PER PACKAGE, and that is the whole difficulty. Asking
"does any package's build carry this directory?" is not good enough, and the
first version of this tool made exactly that mistake: two unrelated packages
copy tests/unit, so every file in tests/unit looked covered -- including the
one whose absence from a build prompted this tool. A checker that would not
have caught the bug that motivated it is worth nothing.

So a test is reached only when one build script BOTH carries the test's own
directory AND carries the source module that test imports. A test of
luma_installer sitting in a directory that only luma-search's build copies is
not covered by luma-search running it, because luma-search does not ship the
code under test.

There are two ways to fail, and they are not equally bad. A test no package
%check reaches but CI does still gates the branch -- but NOT the drop, and the
drop is what reaches a person's machine, because packages are built by
scripts/packages/build-*.sh on the build host and nothing there runs CI. That
is exactly how the rules for release .35 shipped green: CI ran them, the build
did not, and the build is what produced the RPM. A test neither reaches gates
nothing at all.

Usage:  tools/check-unreached-tests.py [--json] [--quiet]
Exit:   0 nothing unreached, 1 something unreached, 2 could not run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Where tests live. A file under one of these whose name looks like a test.
TEST_ROOTS = ("tests", "src")
TEST_NAME = re.compile(r"(^test_.*\.(py|sh|js)$|_test\.py$|^test-.*\.sh$)")

#: A %check that does none of these is not running tests, whatever else it does.
RUNS_TESTS = re.compile(r"(\b(unittest|pytest|meson test|ninja test|make check)\b"
                        r"|(?<![\w/])tests?/[\w./-]*(run|check|suite)[\w./-]*"
                        r"|\b(bash|sh|python3?|node|%\{__python3\})\s+\S*tests?/)")

#: The modules a test exercises: "from luma_installer import x", "import
#: prairie_apps", and the sys.path shim that names a source tree directly.
IMPORTS = re.compile(r"^\s*(?:from|import)\s+([a-z][a-z0-9_]*)", re.M)
PATH_SHIM = re.compile(r"src[/\"']+([A-Za-z0-9._-]+)")


def allowed() -> dict[str, str]:
    """Tests deliberately left ungated, each with the reason someone accepted.

    An entry without a reason is refused. A silent exclusion list is how a
    test ends up gating nothing by accident, which is the thing this tool is
    for; making the reason mandatory keeps an exclusion a decision someone has
    to write down and defend in review.
    """
    path = ROOT / "tools" / "ungated-tests.txt"
    entries: dict[str, str] = {}
    if not path.is_file():
        return entries
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, _, reason = line.partition("#")
        name, reason = name.strip(), reason.strip()
        if not reason:
            print(f"error: {path.name}:{number}: {name} is excluded with no reason.",
                  file=sys.stderr)
            raise SystemExit(2)
        entries[name] = reason
    return entries


def ci_paths() -> set[str]:
    """Directories the CI workflows run tests from.

    CI gates the branch. It does not gate a drop: packages are built by
    scripts/packages/build-*.sh on the build host, and nothing there runs a
    workflow. So CI coverage is worth recording and is not a substitute.
    """
    found: set[str] = set()
    workflows = ROOT / ".github" / "workflows"
    if not workflows.is_dir():
        return found
    for path in sorted(workflows.glob("*.y*ml")):
        found |= paths_in_workflow(path.read_text(errors="replace"))
    return found


def paths_in_workflow(text: str) -> set[str]:
    found: set[str] = set()
    # Line-bounded on purpose: with \s here, a greedy run between two
    # discover invocations spans the newlines separating them and captures
    # only the last, silently dropping every directory named before it.
    for match in re.finditer(r"discover[^\n]*?-s[ \t]+([\w./-]+)", text):
        found.add(match.group(1).rstrip("/"))
    for match in re.finditer(r"(?<![\w/])((?:tests|src)/[\w./-]+\.(?:py|sh))", text):
        found.add(match.group(1))
    return found


def specs() -> list[Path]:
    return sorted((ROOT / "packaging" / "rpm").glob("*.spec"))


def check_body(spec: Path) -> str | None:
    """The %check section, or None if the spec has none."""
    text = spec.read_text(errors="replace")
    match = re.search(r"^%check\b(.*?)(?=^%[a-z]+\b)", text, re.S | re.M)
    return match.group(1) if match else None


def build_scripts_for(spec: Path, scripts: dict[Path, str]) -> list[Path]:
    """Every build script that builds this spec.

    Naming the spec file is the clear case. Some packages are linked only by
    convention -- luma-continuity's builder produces its source tarball and
    never mentions the spec -- so a script whose own name carries the package
    name counts too. Missing that link made this tool claim 68 tests gated
    nothing when they are in fact carried into the tarball the spec builds
    from, which is precisely the kind of confident wrong answer a checker like
    this must not give.
    """
    package = spec.stem
    return sorted(path for path, text in scripts.items()
                  if spec.name in text or package in path.name)


def mentioned_paths(text: str) -> set[str]:
    """Repository paths a build script refers to.

    Matches "$repo_root/<path>", "$repo_root"/<path> and plain repo-relative
    paths, which between them cover every build script in the tree.
    """
    found: set[str] = set()
    for match in re.finditer(r'\$\{?repo_root\}?"?/([A-Za-z0-9_./-]+)', text):
        found.add(match.group(1).rstrip("/."))
    for match in re.finditer(r'(?<![\w/$-])((?:tests|src)/[A-Za-z0-9_./-]+)', text):
        found.add(match.group(1).rstrip("/."))
    return found


def covers(mentioned: set[str], relative: str) -> bool:
    """Does any mentioned path contain this file?"""
    parts = relative.split("/")
    for depth in range(len(parts), 0, -1):
        if "/".join(parts[:depth]) in mentioned:
            return True
    return False


def subject_of(path: Path) -> set[str]:
    """The source modules a test exercises, as a build script would name them.

    A test that imports nothing local exercises nothing we can attribute, and
    is treated as covered by whoever carries it -- guessing would turn a tool
    people should act on into one they argue with.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return set()
    found = {m for m in IMPORTS.findall(text)
             if m.startswith(("luma_", "prairie_")) or m in ("imsd",)}
    found |= {m for m in PATH_SHIM.findall(text) if m.startswith(("luma-", "prairie-"))}
    return found


def carries(text: str, module: str) -> bool:
    """Does this build script carry that source module into its build tree?"""
    return module in text or module.replace("_", "-") in text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true",
                        help="print only the unreached files")
    parser.add_argument("--self-test", action="store_true",
                        help="prove this tool still catches the bug it was written for")
    values = parser.parse_args(argv)

    if values.self_test:
        return self_test()

    scripts = {}
    # Build scripts are not all shell: luma-continuity's is Python, and a glob
    # that assumed .sh silently attributed none of its 76 tests to it.
    candidates = [p for p in (ROOT / "scripts").rglob("*")
                  if p.is_file() and p.suffix in (".sh", ".py", "")]
    for path in sorted(candidates):
        try:
            scripts[path] = path.read_text(errors="replace")
        except OSError:
            continue
    if not scripts:
        print("error: no build scripts found; run this from the repository.",
              file=sys.stderr)
        return 2

    tests: list[str] = []
    for root in TEST_ROOTS:
        for path in sorted((ROOT / root).rglob("*")):
            if not path.is_file() or not TEST_NAME.match(path.name):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if "/node_modules/" in relative or "/__pycache__/" in relative:
                continue
            tests.append(relative)

    # Per package: the paths its build carries, and the text of its build
    # scripts, so a test can be matched against the package that ships the code
    # it is testing rather than against any package at all.
    carried: list[tuple[set[str], str]] = []
    no_check: list[str] = []
    check_runs_nothing: list[str] = []
    no_build_script: list[str] = []

    for spec in specs():
        body = check_body(spec)
        if body is None:
            no_check.append(spec.name)
            continue
        if not RUNS_TESTS.search(body):
            check_runs_nothing.append(spec.name)
            continue
        found = build_scripts_for(spec, scripts)
        if not found:
            # Nobody can rebuild this package from the repository the way every
            # other one is built. That is its own problem, reported separately;
            # it is not evidence about any test file.
            no_build_script.append(spec.name)
            continue
        for script in found:
            carried.append((mentioned_paths(scripts[script]), scripts[script]))

    unreached = []
    for relative in tests:
        holders = [(paths, text) for paths, text in carried if covers(paths, relative)]
        if not holders:
            unreached.append(relative)
            continue
        if relative.startswith("src/"):
            # A test inside a package's own source tree belongs to that
            # package, and its %check discovers the directory wholesale --
            # whatever any individual file happens to import. Carriage settles
            # it. Counting holders instead would be wrong: a shared directory
            # carried by exactly one unrelated package is not coverage, which
            # is a mistake an earlier version of this tool made and which let
            # it miss the very bug it was written for.
            continue
        # Everything under the repository's own tests/ is SHARED. "Somebody
        # carries it" says nothing there: the package that ships the code
        # under test has to be one of them, or the test gates nothing for the
        # package it is about.
        subjects = subject_of(ROOT / relative)
        if not subjects:
            continue
        if not any(carries(text, module) for _paths, text in holders for module in subjects):
            unreached.append(relative)

    ci = ci_paths()
    accepted = allowed()
    unreached = [t for t in unreached if t not in accepted]
    ci_only = [t for t in unreached if covers(ci, t)]
    nowhere = [t for t in unreached if t not in set(ci_only)]

    if values.json:
        print(json.dumps({
            "tests": len(tests),
            "unreached": unreached,
            "gated_nowhere": nowhere,
            "ci_only_not_in_any_package_check": ci_only,
            "specs_without_check": no_check,
            "checks_running_no_tests": check_runs_nothing,
            "specs_without_build_script": no_build_script,
            "deliberately_ungated": allowed(),
        }, indent=2))
        return 1 if unreached else 0

    if not values.quiet:
        print(f"{len(tests)} test files, {len(tests) - len(unreached)} reachable "
              f"by a package %check that runs tests.\n")

    def show(title: str, files: list[str], note: str) -> None:
        if not files:
            return
        print(f"{title} ({len(files)}):")
        print(f"  {note}")
        by_directory: dict[str, list[str]] = {}
        for relative in files:
            by_directory.setdefault(str(Path(relative).parent), []).append(relative)
        for directory in sorted(by_directory):
            names = by_directory[directory]
            print(f"    {directory}/  ({len(names)})")
            for relative in names[:4]:
                print(f"        {Path(relative).name}")
            if len(names) > 4:
                print(f"        ... and {len(names) - 4} more")
        print()

    show("GATED BY NOTHING", nowhere,
         "No package %check and no CI workflow runs these.")
    show("GATED BY CI BUT NOT BY THE PACKAGE THAT SHIPS THEM", ci_only,
         "These stop a branch and do NOT stop a drop. Packages are built on the\n"
         "  build host, where no workflow runs, so a green build means nothing\n"
         "  about them. This is the class that let release .35 ship.")

    if not values.quiet and accepted:
        print(f"deliberately ungated, with a reason on record ({len(accepted)}):")
        for name, reason in sorted(accepted.items()):
            print(f"  {name}\n      {reason}")
        print()

    if not values.quiet:
        for title, names in (("specs with no %check at all", no_check),
                             ("%check runs no test suite (may still verify other things)",
                              check_runs_nothing),
                             ("specs no build script builds", no_build_script)):
            if names:
                print(f"{title} ({len(names)}): {', '.join(sorted(names))}\n")

    return 1 if unreached else 0


def self_test() -> int:
    """A checker that cannot catch its own motivating bug is worth nothing.

    Two earlier versions of this tool passed over it: the first because any
    package carrying tests/unit made every file in it look covered, the second
    because a shared directory carried by exactly one unrelated package was
    treated as unambiguous. Both looked right and reported green. So the case
    is asserted here rather than trusted.
    """
    failures = []

    # The real case: a test of luma_installer, living in the repository's
    # shared tests/unit, carried only by a build that does not ship
    # luma_installer. It must be reported.
    shared = "tests/unit/test_capsule_runtime.py"
    if not (ROOT / shared).exists():
        print(f"self-test: cannot run, {shared} is gone", file=sys.stderr)
        return 2
    subjects = subject_of(ROOT / shared)
    if "luma_installer" not in subjects:
        failures.append(f"the subject of {shared} was not recognised: {subjects}")
    if carries("cp -R $repo_root/src/luma-search/luma_search .", "luma_installer"):
        failures.append("a build that ships luma_search was credited with luma_installer")
    if not carries("cp -R $repo_root/src/luma-installer/luma_installer .", "luma_installer"):
        failures.append("a build that ships luma_installer was not credited with it")

    # Every directory CI names must be found, not just the last one. A greedy
    # pattern that spanned newlines between two `discover` invocations captured
    # only the second and silently reclassified 45 files as gated by nothing.
    # The self-test passed throughout, which is why this case is now in it.
    # A fixture, not the live workflow: the parser is what is under test, and
    # the drop gate runs this self-test over older revisions whose workflow
    # did not run tests/depot yet, which says nothing about the parser.
    found = paths_in_workflow(
        "        run: dbus-run-session -- python3 -m unittest discover -s tests/unit -p 'test_*.py'\n"
        "      - name: Run tests of the repository's own scripts\n"
        "        run: python3 -m unittest discover -s tests/depot -p 'test_*.py'\n"
    )
    for expected in ("tests/unit", "tests/depot"):
        if expected not in found:
            failures.append(f"CI runs {expected} and the parser did not see it: {found}")

    # A package's own tests belong to it, whatever they import.
    if not covers({"src/luma-energy"}, "src/luma-energy/tests/test_luma_energy.py"):
        failures.append("a package's own tests directory was not recognised as covered")

    if failures:
        for line in failures:
            print(f"self-test FAILED: {line}", file=sys.stderr)
        return 1
    print("self-test passed: the motivating case is still detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
