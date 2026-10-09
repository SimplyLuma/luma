#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Every Luma package the desktop pins must name the release this tree's own
# spec builds.
#
# A pin ahead of its spec means the shipped package was built outside the tree
# and cannot be rebuilt from this revision. That is how luma-developer-platform
# 1.luma.64 and 1.luma.65 shipped: the Release line was edited inside a build
# directory on the builder, the pins moved, and the spec in the repository
# stayed at 1.luma.62 with a %changelog stopping at 1.luma.45. The platform is
# what every other package builds against, so nothing downstream was
# reproducible either, and the one assertion that would have caught it was not
# in the release gate.
#
#   package-release-contract.sh [REPO_ROOT]
#
# It reads config/desktop/inputs.env and config/desktop/packages.txt, resolves
# every image and optional Depot pin that looks like NAME-VERSION-RELEASE.DIST.ARCH, and compares it
# with Name/Version/Release in packaging/rpm. Packages this tree has no spec
# for (upstream Fedora packages, and the Fedora specs Luma carries as patch
# series, which have their own release-synchronisation checks) are reported and
# not judged here.

set -euo pipefail

repo_root=${1:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)}

python3 - "$repo_root" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
spec_dir = root / "packaging" / "rpm"
inputs = root / "config" / "desktop" / "inputs.env"
packages = root / "config" / "desktop" / "packages.txt"

for path in (spec_dir, inputs, packages):
    if not path.exists():
        sys.exit(f"FAIL  package release contract: {path} is missing")

# Every binary package name a spec in this tree produces, with the version and
# release it produces it at. A subpackage declared as "%package sdk" is
# <Name>-sdk; "%package -n luma-x" is named outright.
built = {}
for spec in sorted(spec_dir.glob("*.spec")):
    name = version = release = None
    subpackages = []
    for line in spec.read_text().splitlines():
        if line.startswith("%changelog"):
            break
        field = re.match(r"^(Name|Version|Release):\s*(\S+)", line)
        if field:
            key, value = field.group(1), field.group(2).replace("%{?dist}", "")
            if key == "Name" and name is None:
                name = value
            elif key == "Version" and version is None:
                version = value
            elif key == "Release" and release is None:
                release = value
            continue
        sub = re.match(r"^%package\s+(?:-n\s+(\S+)|(\S+))\s*$", line)
        if sub:
            subpackages.append((sub.group(1), sub.group(2)))
    if not (name and version and release):
        continue
    # A spec may state its name, version or release through macros this reader
    # does not expand. Such a spec cannot be compared with a literal pin, so
    # the pins that use it are reported as unresolved rather than judged.
    if any("%" in field for field in (name, version, release)):
        continue
    built[name] = ("packaging/rpm/" + spec.name, version, release)
    for explicit, suffix in subpackages:
        sub_name = explicit if explicit else f"{name}-{suffix}"
        sub_name = sub_name.replace("%{name}", name)
        if "%" in sub_name:
            continue
        built[sub_name] = ("packaging/rpm/" + spec.name, version, release)

# The native desktop patch specs require these shared subpackages at exactly
# the main package's release. Judge them here too: an otherwise current main
# pin with yesterday's shared files cannot be installed by the image solver.
for family, names in {
    "mutter": ("mutter", "mutter-common"),
    "gnome-control-center": ("gnome-control-center", "gnome-control-center-filesystem"),
}.items():
    patch = root / "patches" / family / "0000-luma-fedora-spec.patch"
    if not patch.exists():
        continue
    text = patch.read_text()
    version_match = re.search(r"^ Version:\s*(\S+)", text, re.M)
    release_match = re.search(r"^\+Release:\s*(\S+)", text, re.M)
    if not version_match or not release_match:
        sys.exit(f"FAIL  package release contract: cannot resolve {patch.relative_to(root)}")
    version = version_match.group(1)
    release = release_match.group(1).replace("%{?dist}", "")
    for name in names:
        built[name] = (str(patch.relative_to(root)), version, release)

# A pin: NAME-VERSION-RELEASE.ARCH, where RELEASE usually ends in the .fcNN
# disttag.
pin = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9._+-]*?)"
    r"-(?P<version>[0-9][A-Za-z0-9._+~]*)"
    # The Fedora disttag is optional: an upstream RPM such as Viola's carries
    # none, and a pin the pattern cannot see is not judged at all.
    r"-(?P<release>[A-Za-z0-9._+~]+?)"
    r"\.(?P<arch>x86_64|aarch64|noarch|src)$"
)

def pins():
    for lineno, line in enumerate(inputs.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        variable, value = line.split("=", 1)
        for token in re.split(r"[\s:,]+", value.strip().strip("'\"")):
            if token:
                yield f"config/desktop/inputs.env:{lineno} {variable}", token
    for pin_file in (packages, root / "config/depot/packages.txt"):
        if not pin_file.exists():
            continue
        for lineno, line in enumerate(pin_file.read_text().splitlines(), 1):
            line = line.strip()
            if line and not line.startswith("#"):
                yield f"{pin_file.relative_to(root)}:{lineno}", line

# Releases that must never ship again (config/desktop/package-denylist.txt).
denied = []
denylist = root / "config/desktop/package-denylist.txt"
if denylist.exists():
    rules = []
    for lineno, line in enumerate(denylist.read_text().splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split(None, 3)
        rules.append((fields[0], re.compile(fields[1]), fields[3] if len(fields) > 3 else "", lineno))
    for where, token in pins():
        for name, pattern, reason, lineno in rules:
            rest = token[len(name) + 1:] if token.startswith(name + "-") else None
            if rest and pattern.match(rest):
                denied.append((where, token, reason, lineno))
if denied:
    print(f"FAIL  {len(denied)} pin(s) name a release that must never ship again:")
    for where, token, reason, lineno in denied:
        print(f"  {token}  ({where})")
        print(f"    denied at config/desktop/package-denylist.txt:{lineno}: {reason}")
    sys.exit(1)

drift = {}
unresolved = {}
unresolved_pins = set()
checked = 0
for where, token in pins():
    matched = pin.match(token)
    if not matched:
        continue
    name = matched.group("name")
    version = matched.group("version")
    release = matched.group("release")
    # The disttag is applied by the builder, not written in the spec.
    release = release.rsplit(".fc", 1)[0]
    if name not in built:
        unresolved.setdefault(name, where)
        unresolved_pins.add((name, version, release))
        continue
    checked += 1
    spec_name, spec_version, spec_release = built[name]
    if (version, release) != (spec_version, spec_release):
        entry = drift.setdefault(
            (name, version, release),
            {"spec": f"{spec_version}-{spec_release}", "file": spec_name, "where": []},
        )
        entry["where"].append(where)

# Recorded exceptions (config/desktop/package-release-exceptions.txt): a pin
# this tree knowingly cannot rebuild, named exactly, with an owner, an expiry
# and a reason. It is either a pin ahead of its spec here, or a pinned package
# whose source lives outside this repository (no spec here). It is reported,
# not failed. An exception fails when it no longer matches such a pin, so none
# outlives the reason it was written for and an excepted package that moves to
# another release is judged again, and it fails from its expiry date on, so a
# deadline forces the source question to be answered.
import datetime
import os

today = os.environ.get("LUMA_CONTRACT_TODAY") or datetime.datetime.now(datetime.timezone.utc).date().isoformat()
problems = []
excepted = {}
exceptions_file = root / "config" / "desktop" / "package-release-exceptions.txt"
if exceptions_file.exists():
    for lineno, line in enumerate(exceptions_file.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        where = f"config/desktop/package-release-exceptions.txt:{lineno}"
        fields = line.split(None, 4)
        if (len(fields) < 5 or "-" not in fields[1]
                or not re.fullmatch(r"-|\d{4}-\d{2}-\d{2}", fields[3])):
            problems.append(f"{where}: expected: NAME VERSION-RELEASE OWNER EXPIRES(YYYY-MM-DD or -) REASON")
            continue
        name, version_release, owner, expires, reason = fields
        version, release = version_release.split("-", 1)
        key = (name, version, release)
        if expires != "-" and today >= expires:
            problems.append(
                f"{where}: the exception for {name} {version_release} expired on {expires} "
                f"(owner {owner}); land its source or remove the package"
            )
            continue
        if key in drift:
            excepted[key] = (drift.pop(key)["spec"], owner, expires, reason, lineno)
        elif key in unresolved_pins:
            unresolved.pop(name, None)
            excepted[key] = ("no spec in this tree", owner, expires, reason, lineno)
        else:
            problems.append(
                f"{where}: {name} {version_release} is not a drifting or out-of-tree pin any more; "
                "remove the exception"
            )

if unresolved:
    print(
        "note  no spec in this tree builds these pinned packages; "
        "they are judged by their own checks:"
    )
    for name in sorted(unresolved):
        print(f"        {name}  ({unresolved[name]})")

if excepted:
    print(f"\nnote  {len(excepted)} recorded exception(s), pins this tree knowingly cannot build:")
    for (name, version, release) in sorted(excepted):
        spec, owner, expires, reason, lineno = excepted[(name, version, release)]
        builds = spec if spec == "no spec in this tree" else f"spec builds {spec}"
        until = "no expiry" if expires == "-" else f"expires {expires}"
        print(f"  {name}  pinned {version}-{release}, {builds}")
        print(f"    owner {owner}, {until}: {reason}")
        print(f"    recorded at config/desktop/package-release-exceptions.txt:{lineno}")

if problems:
    print(f"\nFAIL  {len(problems)} problem(s) with the recorded exceptions:")
    for problem in problems:
        print(f"    {problem}")

if drift:
    print(f"\nFAIL  {len(drift)} pinned package(s) name a release this tree cannot build:")
    for (name, version, release) in sorted(drift):
        entry = drift[(name, version, release)]
        print(f"  {name}")
        print(f"    pinned at   {version}-{release}")
        print(f"    spec builds {entry['spec']}  ({entry['file']})")
        for where in entry["where"]:
            print(f"    pinned by   {where}")
    print(
        "\nEither land the source the shipped release was built from and bring\n"
        "the spec Release and %changelog with it, or move the pin back to the\n"
        "release the spec builds. A package that only exists as an artifact on\n"
        "a builder is not a release."
    )
    sys.exit(1)

if problems:
    sys.exit(1)

print(
    f"PASS  package release contract: {checked - len(excepted)} pinned package(s) "
    f"match their spec, {len(excepted)} recorded exception(s)"
)
PY

# Release note fragments (docs/os/release-process.md): every changes/*.md must
# parse, with a category, component, Summary and Details, and name only packages
# this tree pins. Whether a release's changed packages are all covered is
# checked when it is published (scripts/os/nightly.sh, release_notes.py build).
python3 "$repo_root/scripts/os/lib/release_notes.py" validate --repo "$repo_root" || exit 1
python3 - "$repo_root" <<'PY'
import re, sys
from pathlib import Path
root = Path(sys.argv[1])
pinned = set()
for path in (root / "config/desktop/packages.txt", root / "config/desktop/inputs.env", root / "config/os/media.env", root / "config/depot/packages.txt"):
    if not path.exists():
        continue
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+._-]*-[^-\s=]+-[^-\s=]+\.(?:x86_64|noarch|aarch64)", path.read_text()):
        pinned.add(re.match(r"^(.+)-[^-]+-[^-]+\.[a-z0-9_]+$", token).group(1))
problems = []
for fragment in sorted((root / "changes").glob("*.md")):
    match = re.search(r"^packages:\s*\[(.*)\]\s*$", fragment.read_text(), re.M)
    for name in [p.strip() for p in (match.group(1).split(",") if match else []) if p.strip()]:
        if name not in pinned and name != "luma-messages-bridges":
            problems.append(f"{fragment.relative_to(root)}: {name} is not a package this tree pins")
for problem in problems:
    print(f"    {problem}")
print(f"{'FAIL' if problems else 'PASS'}  release note fragments name pinned packages")
sys.exit(1 if problems else 0)
PY
