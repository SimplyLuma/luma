#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Release notes from changelog fragments (docs/os/release-process.md).

  release_notes.py validate [FRAGMENT...]
      Check the syntax of changes/*.md (all of them when none are named).

  release_notes.py build --repo ROOT --from REV --to REV
                         --previous-manifest FILE --manifest FILE
                         --channel C --build-id ID --version V --nightly-date YYYY-MM-DD
                         [--exceptions FILE] [--out FILE]
      Assemble the notes of a release. Included fragments are the changes/*.md
      files present at --to and not at --from (the previous published release's
      source). Every pinned package whose release differs between the two
      luma-packages.manifest files ("NEVRA sha256 header-sha256" lines), or that
      was added or removed, must be named in the packages list of an included
      fragment, or be recorded in --exceptions as a pure rebuild with no
      user-visible change ("NAME VERSION-RELEASE REASON"). Otherwise nothing is
      written and the exit status is 1: the release must not be published.

Fragment format (changes/<topic>.md):

  ---
  type: feature|improvement|fix|security
  component: Dock            (optional, internal only; never shown)
  packages: [gnome-shell, gnome-shell-common]
  highlight: true            (optional: leads the release notes)
  ---
  Summary: Added mute and deafen buttons to Live Activities
  Details: One to three short sentences with more depth, not repeating the headline.

The Summary is a short release-note headline (usually 3-8 words). Headlines
over 10 words, and names from config/os/release-notes-lint.txt (third-party
apps a change is not about), are reported as lint warnings.
"""

import argparse
import datetime
import json
import re
import subprocess
import os
import sys
from pathlib import Path

TYPES = ("feature", "improvement", "fix", "security")
TYPE_LABELS = {"feature": "Features", "improvement": "Improvements", "fix": "Fixes", "security": "Security"}
HEADLINE_WORDS = 10
NEVRA = re.compile(r"^(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>x86_64|noarch|aarch64|i686)$")


def parse_fragment(text, where):
    problems = []
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not match:
        return None, [f"{where}: no front matter between --- lines"]
    meta = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            problems.append(f"{where}: front matter line without a colon: {line!r}")
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    body = match.group(2)
    summary = re.search(r"^Summary:\s*(.+?)\s*$", body, re.M)
    details = re.search(r"^Details:\s*(.+?)(?=^\w+:|\Z)", body, re.M | re.S)
    kind = meta.get("type", "")
    if "category" in meta:
        problems.append(f"{where}: 'category' is retired; use type: {'|'.join(TYPES)}")
    if kind not in TYPES:
        problems.append(f"{where}: type must be exactly one of {', '.join(TYPES)}")
    packages = []
    raw = meta.get("packages", "")
    if raw:
        if not (raw.startswith("[") and raw.endswith("]")):
            problems.append(f"{where}: packages must be a [list]")
        else:
            packages = [p.strip() for p in raw[1:-1].split(",") if p.strip()]
    if not summary:
        problems.append(f"{where}: Summary: line is missing")
    elif len(summary.group(1)) > 120:
        problems.append(f"{where}: Summary is a headline, not a sentence (over 120 characters)")
    if not details:
        problems.append(f"{where}: Details: is missing")
    fragment = {
        "highlight": meta.get("highlight", "").lower() == "true",
        "type": kind,
        "component": meta.get("component", ""),
        "packages": packages,
        "summary": summary.group(1) if summary else "",
        "details": " ".join(details.group(1).split()) if details else "",
    }
    return fragment, problems


def lint_fragment(fragment, where, names):
    """Warnings, not errors: long headlines and third-party app names."""
    warnings = []
    words = len(fragment["summary"].split())
    if words > HEADLINE_WORDS:
        warnings.append(f"{where}: headline has {words} words (aim for 3-8)")
    text = f"{fragment['summary']} {fragment['details']}"
    for name in names:
        if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text):
            warnings.append(f"{where}: names '{name}'; name third-party apps only when the change is about that app")
    return warnings


def lint_names(repo):
    path = Path(repo) / "config/os/release-notes-lint.txt"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def git(repo, *args):
    return subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", repo, *args],
                          check=True, capture_output=True, text=True).stdout


def fragments_at(repo, rev):
    out = git(repo, "ls-tree", "-r", "--name-only", rev, "--", "changes/")
    return {line for line in out.splitlines() if line.endswith(".md") and "/" not in line[len("changes/"):]}


def manifest_pins(path):
    pins = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = NEVRA.match(line.split()[0])
        if match:
            pins[match["name"]] = f"{match['version']}-{match['release']}"
    return pins


def cmd_validate(args):
    root = Path(args.repo)
    files = [Path(f) for f in args.fragments] or sorted((root / "changes").glob("*.md"))
    problems, warnings, names = [], [], lint_names(args.repo)
    for path in files:
        if path.name == "README.md":
            continue
        fragment, found = parse_fragment(path.read_text(encoding="utf-8"), str(path))
        problems.extend(found)
        if fragment and not found:
            warnings.extend(lint_fragment(fragment, str(path), names))
    for problem in problems:
        print(problem)
    for warning in warnings:
        print(f"LINT  {warning}")
    print(f"{'FAIL' if problems else 'PASS'}  release note fragments: {len(files)} checked, {len(problems)} problem(s), {len(warnings)} lint warning(s)")
    return 1 if problems else 0


def lead_sentence(fragments):
    """One plain sentence for the page lead: the most notable change, then a count.

    A fragment may set `highlight: true` to be chosen; otherwise the first
    added item leads, then fixed, changed, security, removed.
    """
    if not fragments:
        return "This build carries no user-visible changes."
    order = ["feature", "improvement", "fix", "security"]
    chosen = next((f for f in fragments if f.get("highlight")), None) or \
        sorted(fragments, key=lambda f: order.index(f["type"]))[0]
    lead = chosen["summary"].rstrip(".")
    others = len(fragments) - 1
    return f"{lead}, plus {others} more change{'s' if others != 1 else ''}." if others else f"{lead}."


def cmd_build(args):
    repo = args.repo
    membership = args.membership or args.to_rev
    included = sorted(fragments_at(repo, membership) - (fragments_at(repo, args.from_rev) if args.from_rev else set()))
    fragments, problems = [], []
    for name in included:
        if name.endswith("README.md"):
            continue
        try:
            text = git(repo, "show", f"{args.to_rev}:{name}")
        except subprocess.CalledProcessError:
            problems.append(f"{name}: included at {membership} but missing at {args.to_rev}")
            continue
        fragment, found = parse_fragment(text, name)
        problems.extend(found)
        if fragment:
            fragment["file"] = name
            fragments.append(fragment)
    previous = manifest_pins(args.previous_manifest) if args.previous_manifest else {}
    current = manifest_pins(args.manifest)
    changed = sorted(n for n in set(previous) | set(current) if previous.get(n) != current.get(n))
    exceptions = {}
    if args.exceptions and Path(args.exceptions).exists():
        for line in Path(args.exceptions).read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                name, vr, reason = (line.split(None, 2) + [""])[:3]
                exceptions[(name, vr)] = reason
    covered = {p for f in fragments for p in f["packages"]}
    uncovered = []
    for name in changed:
        if name in covered or (name, current.get(name) or "removed") in exceptions:
            continue
        uncovered.append(f"{name}: {previous.get(name) or 'new'} -> {current.get(name) or 'removed'}")
    for name in sorted(covered - set(changed)):
        pass  # a fragment may describe a change carried by an unchanged package's configuration
    if problems or uncovered:
        for problem in problems:
            print(f"FAIL  {problem}")
        for item in uncovered:
            print(f"FAIL  no release note fragment names the changed package {item}")
        print(f"FAIL  release notes: {len(uncovered)} changed package(s) without a fragment, {len(problems)} fragment problem(s); nothing may be published")
        return 1
    order = {c: i for i, c in enumerate(TYPES)}
    fragments.sort(key=lambda f: (order[f["type"]], not f["highlight"], f["summary"].lower()))
    sections = {c: [] for c in TYPES}
    for f in fragments:
        sections[f["type"]].append({"summary": f["summary"], "details": f["details"]})
    notes = {
        "schema": "org.projectluma.os-release-notes/v2",
        "section_labels": TYPE_LABELS,
        "section_order": list(TYPES),
        "channel": args.channel,
        "build_id": args.build_id,
        "version": args.version,
        "nightly_date": args.nightly_date,
        # The release's name as people see it (ADR-040), for titles.
        "display_name": args.display_name or display_name_for(args.channel, args.nightly_date),
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": args.summary or lead_sentence(fragments),
        "sections": sections,
        "packages_changed": [{"name": n, "from": previous.get(n), "to": current.get(n)} for n in changed],
    }
    text = json.dumps(notes, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    print(f"PASS  release notes: {len(fragments)} fragment(s) cover {len(changed)} changed package(s)", file=sys.stderr)
    return 0


def display_name_for(channel, nightly_date):
    """A build from before release names recorded none: name it from the identity."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import release_identity
    return release_identity.display_name(release_identity.load(), channel, nightly_date)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--repo", default=".")
    v.add_argument("fragments", nargs="*")
    b = sub.add_parser("build")
    b.add_argument("--repo", required=True)
    b.add_argument("--from", dest="from_rev", default="")
    b.add_argument("--to", dest="to_rev", required=True)
    b.add_argument("--membership", default="", help="decide which fragments belong at this revision (default --to); read their text at --to")
    b.add_argument("--previous-manifest", default="")
    b.add_argument("--manifest", required=True)
    b.add_argument("--exceptions", default="")
    b.add_argument("--channel", required=True)
    b.add_argument("--build-id", required=True)
    b.add_argument("--version", required=True)
    b.add_argument("--nightly-date", required=True)
    b.add_argument("--display-name", default="", help="the build's LUMA_DISPLAY_NAME; derived from the release identity when empty")
    b.add_argument("--summary", default="")
    b.add_argument("--out", default="")
    args = parser.parse_args(argv)
    return cmd_validate(args) if args.command == "validate" else cmd_build(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
