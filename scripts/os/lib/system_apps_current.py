#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Keep system apps current in every nightly (config/desktop/system-apps.txt).

  system_apps_current.py pins --source ROOT --pool DIR --incoming DIR
      Fail when a system app's pin in config/desktop/packages.txt is older than
      the newest build of that package that was released: in the verified pool,
      in a delivered incoming drop (drops with a HOLD file are skipped), or in
      the app's own signed release directories.

  system_apps_current.py notes --repo ROOT --from REV --to REV
                         --previous-manifest FILE --manifest FILE
      Fail when a system app's version-release changed between two
      luma-packages.manifest files and no changes/*.md fragment added in
      (REV, REV] names the package with type improvement and a headline
      "Updated <App> to <version>".

  system_apps_current.py pr --repo ROOT --base REV [--head REV]
      The same rule for one pull request, before anything is built: when the
      change (BASE, HEAD] moves a system app's pin (config/desktop/packages.txt,
      config/desktop/inputs.env) or its spec's Version/Release (including the
      macros they expand), a fragment added in that change must satisfy notes.

Each finding is one "FAIL ..." or "PASS ..." line; the exit status is 1 on any
failure.
"""

import argparse
import pathlib
import re
import subprocess
import sys

# python3-rpm is only needed to read and compare RPM headers (pins); the notes
# rules run on a plain CI runner.
try:
    import rpm
except ImportError:
    rpm = None

PIN_FILES = ('config/desktop/packages.txt', 'config/desktop/inputs.env')


def split_nevra(nevra):
    base, arch = nevra.rsplit('.', 1)
    name_version, release = base.rsplit('-', 1)
    name, version = name_version.rsplit('-', 1)
    epoch = '0'
    if ':' in version:
        epoch, version = version.split(':', 1)
    return name, epoch, version, release, arch


def evr_text(evr):
    epoch, version, release = evr
    return (f'{epoch}:' if epoch not in ('0', '', None) else '') + f'{version}-{release}'


def load_apps(source):
    apps = []
    path = source / 'config/desktop/system-apps.txt'
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) < 2:
            raise SystemExit(f'{path}:{lineno}: expected "PACKAGE DISPLAY-NAME [RELEASE-DIR...]"')
        apps.append({'package': fields[0], 'display': fields[1].replace('_', ' '),
                     'release_dirs': [pathlib.Path(p) for p in fields[2:]]})
    return apps


def header_evr(path, ts):
    with open(path, 'rb') as stream:
        header = ts.hdrFromFdno(stream.fileno())
    epoch = header[rpm.RPMTAG_EPOCH]
    return header[rpm.RPMTAG_NAME], (str(epoch) if epoch is not None else '0',
                                     header[rpm.RPMTAG_VERSION], header[rpm.RPMTAG_RELEASE])


def newest(candidates):
    best = None
    for evr, where in candidates:
        if best is None or rpm.labelCompare(evr, best[0]) > 0:
            best = (evr, where)
    return best


def cmd_pins(args):
    if rpm is None:
        raise SystemExit('system_apps_current.py pins needs python3-rpm')
    source = pathlib.Path(args.source)
    pins = {}
    for raw in (source / 'config/desktop/packages.txt').read_text().splitlines():
        line = raw.split('#', 1)[0].strip()
        if line:
            name, epoch, version, release, _arch = split_nevra(line)
            pins[name] = (epoch, version, release)
    ts = rpm.TransactionSet()
    ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES | rpm._RPMVSF_NODIGESTS)
    failures = 0
    for app in load_apps(source):
        name = app['package']
        if name not in pins:
            print(f"FAIL {name}: system app is not pinned in config/desktop/packages.txt")
            failures += 1
            continue
        candidates = []
        manifest = pathlib.Path(args.pool) / 'pool.manifest'
        if manifest.is_file():
            for line in manifest.read_text().splitlines():
                if not line.strip():
                    continue
                pname, epoch, version, release, _arch = split_nevra(line.split()[0])
                if pname == name:
                    candidates.append(((epoch, version, release), 'the verified pool'))
        roots = []
        incoming = pathlib.Path(args.incoming)
        if incoming.is_dir():
            roots += [drop for drop in sorted(incoming.iterdir())
                      if drop.is_dir() and not (drop / 'HOLD').exists()]
        roots += [d for d in app['release_dirs'] if d.is_dir()]
        for root in roots:
            for path in root.rglob(f'{name}-*.rpm'):
                if path.name.endswith('.src.rpm'):
                    continue
                try:
                    hname, evr = header_evr(path, ts)
                except (OSError, rpm.error) as error:
                    print(f"WARN {name}: unreadable {path}: {error}")
                    continue
                if hname == name:
                    candidates.append((evr, str(path)))
        best = newest(candidates)
        pinned = pins[name]
        if best and rpm.labelCompare(best[0], pinned) > 0:
            print(f"FAIL {name}: pinned {evr_text(pinned)} is older than the released "
                  f"{evr_text(best[0])} ({best[1]}); pin it with a changelog fragment "
                  f"\"Updated {app['display']} to ...\"")
            failures += 1
        else:
            print(f"PASS {name}: pinned {evr_text(pinned)} is the newest released build")
    return 1 if failures else 0


def manifest_evrs(path):
    evrs = {}
    if path and pathlib.Path(path).is_file():
        for line in pathlib.Path(path).read_text().splitlines():
            if line.strip():
                name, epoch, version, release, _arch = split_nevra(line.split()[0])
                evrs[name] = (epoch, version, release)
    return evrs


def front_matter(text):
    match = re.match(r'---\n(.*?)\n---\n(.*)', text, re.S)
    if not match:
        return {}, text
    fields = {}
    for line in match.group(1).splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields[key.strip()] = value.strip()
    return fields, match.group(2)


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout


def git_file(repo, rev, path):
    """PATH's text at REV, or None when it does not exist there."""
    result = subprocess.run(['git', '-C', str(repo), 'show', f'{rev}:{path}'], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def added_fragments(repo, from_rev, to_rev):
    """(path, type, packages, summary) of each changes/*.md added in (FROM, TO]."""
    if from_rev:
        added = git(repo, 'diff', '--name-only', '--diff-filter=A', f'{from_rev}..{to_rev}', '--', 'changes/').split()
    else:
        added = git(repo, 'ls-tree', '--name-only', to_rev, 'changes/').split()
    fragments = []
    for name in added:
        if not name.endswith('.md'):
            continue
        fields, body = front_matter(git(repo, 'show', f'{to_rev}:{name}'))
        packages = [p.strip() for p in fields.get('packages', '').strip('[]').split(',') if p.strip()]
        summary = next((line[len('Summary:'):].strip() for line in body.splitlines()
                        if line.startswith('Summary:')), '')
        fragments.append((name, fields.get('type', ''), packages, summary))
    return fragments


def note_findings(apps, fragments, changed):
    """One PASS/FAIL line per system app in CHANGED ({package: what changed})."""
    failures = 0
    for app in apps:
        name = app['package']
        if name not in changed:
            continue
        headline = re.compile(rf"^Updated {re.escape(app['display'])} to \S+")
        matching = [f for f in fragments if name in f[2] and f[1] == 'improvement' and headline.match(f[3])]
        if matching:
            print(f"PASS {name}: {changed[name]} is noted in {matching[0][0]}")
        else:
            print(f"FAIL {name}: {changed[name]} without a new changes/*.md fragment "
                  f"(type: improvement, packages: [{name}], Summary: Updated {app['display']} to X.Y)")
            failures += 1
    return failures


def cmd_notes(args):
    repo = pathlib.Path(args.repo)
    previous = manifest_evrs(args.previous_manifest)
    current = manifest_evrs(args.manifest)
    changed = {name: f"{evr_text(previous.get(name, ('0', '-', '-')))} -> {evr_text(evr)}"
               for name, evr in current.items() if previous.get(name) != evr}
    failures = note_findings(load_apps(repo), added_fragments(repo, args.from_rev, args.to_rev), changed)
    return 1 if failures else 0


def pinned_versions(text, name):
    """Every NAME-VERSION-RELEASE.ARCH pin of NAME in a pin file's text."""
    if text is None:
        return set()
    pin = re.compile(rf"(?<![\w.+-]){re.escape(name)}-([0-9][^\s'\":,=]*\.(?:x86_64|aarch64|noarch))(?![\w.+-])")
    return {f'{name}-{match}' for line in text.splitlines()
            for match in pin.findall(line.split('#', 1)[0])}


def spec_identity(text):
    """A spec's Name, its Version and Release lines, and every macro they
    expand (recursively), with all of that macro's definitions; None without a
    spec. Arch-conditional definitions are all kept, so a change in any arm
    counts."""
    if text is None:
        return None
    preamble = re.split(r'^%(?:description|changelog)\b', text, maxsplit=1, flags=re.M)[0]
    fields, macros = {}, {}
    for line in preamble.splitlines():
        field = re.match(r'^(Name|Version|Release):\s*(\S+)', line)
        if field:
            fields.setdefault(field.group(1), field.group(2))
        definition = re.match(r'^%(?:global|define)\s+(\w+)\s+(.*?)\s*$', line)
        if definition:
            macros.setdefault(definition.group(1), []).append(definition.group(2))
    seen, pending = {}, [fields.get('Version', ''), fields.get('Release', '')]
    while pending:
        for macro in re.findall(r'%\{?[?!]*(\w+)', pending.pop()):
            if macro in macros and macro not in seen:
                seen[macro] = tuple(macros[macro])
                pending.extend(macros[macro])
    return (fields.get('Name'), fields.get('Version'), fields.get('Release'), tuple(sorted(seen.items())))


def pr_changes(repo, base, head, apps):
    """{package: what changed} for each system app whose pin or spec release
    differs between BASE and HEAD."""
    names = {app['package'] for app in apps}
    changed = {}
    for path in PIN_FILES:
        before, after = git_file(repo, base, path), git_file(repo, head, path)
        for name in sorted(names):
            old, new = pinned_versions(before, name), pinned_versions(after, name)
            if old != new:
                changed.setdefault(name, f"pin in {path} {', '.join(sorted(old)) or 'none'} -> "
                                         f"{', '.join(sorted(new)) or 'none'}")
    specs = git(repo, 'diff', '--name-only', f'{base}..{head}', '--', 'packaging/rpm/').split()
    for path in specs:
        if not path.endswith('.spec'):
            continue
        before, after = spec_identity(git_file(repo, base, path)), spec_identity(git_file(repo, head, path))
        name = (after or before)[0]
        if name in names and before != after:
            changed.setdefault(name, f"release in {path}")
    return changed


def cmd_pr(args):
    repo = pathlib.Path(args.repo)
    apps = load_apps(repo)
    changed = pr_changes(repo, args.base, args.head, apps)
    if not changed:
        print('PASS no system app pin or release changed')
        return 0
    failures = note_findings(apps, added_fragments(repo, args.base, args.head), changed)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    pins = sub.add_parser('pins')
    pins.add_argument('--source', required=True)
    pins.add_argument('--pool', required=True)
    pins.add_argument('--incoming', required=True)
    notes = sub.add_parser('notes')
    notes.add_argument('--repo', required=True)
    notes.add_argument('--from', dest='from_rev', default='')
    notes.add_argument('--to', dest='to_rev', default='HEAD')
    notes.add_argument('--previous-manifest', default='')
    notes.add_argument('--manifest', required=True)
    pr = sub.add_parser('pr')
    pr.add_argument('--repo', required=True)
    pr.add_argument('--base', required=True)
    pr.add_argument('--head', default='HEAD')
    args = parser.parse_args()
    return {'pins': cmd_pins, 'notes': cmd_notes, 'pr': cmd_pr}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
