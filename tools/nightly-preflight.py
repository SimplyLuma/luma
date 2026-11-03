#!/usr/bin/python3 -I
# SPDX-License-Identifier: Apache-2.0
"""Find every pinned RPM a nightly needs that its pool lacks, and fetch it from
the CI build of the pull request that set the pin.

  nightly-preflight.py --sha SHA --have FILE --out DIR

--have lists the NEVRAs already available (one per line, extra columns
ignored), for example the pool manifest plus the loose directory. Fetched
RPMs land in --out; their headers are verified when the pool admits them.
Exit status 1 when a pin is still missing, naming the commit that set it.
"""
import argparse
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

REPO = 'SimplyLuma/luma'
CONFIG = ('config/desktop/packages.txt', 'config/desktop/inputs.env')


def run(*args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def gh(path):
    return json.loads(run('gh', 'api', path))


def pins(root, sha):
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(['git', '-C', str(root), 'archive', sha, 'config', 'scripts/os/lib'],
                                 check=True, capture_output=True).stdout
        tarfile.open(fileobj=BytesIO(archive)).extractall(tmp, filter='data')
        result = set()
        for arch, profile in (('x86_64', 'full'), ('aarch64', 'arm-preview')):
            result |= set(run(sys.executable, '-I', f'{tmp}/scripts/os/lib/desktop_target.py',
                              '--root', tmp, '--arch', arch, '--profile', profile, 'pins').split())
        return result


def setter(root, sha, nevra):
    """The first-parent main commit that introduced the pin. ARM pins are often
    the x86 line with its architecture swapped, so match without it."""
    stem = nevra.rsplit('.', 1)[0]
    out = run('git', '-C', str(root), 'log', '--first-parent', '--format=%H', '-S', stem, sha, '--', *CONFIG)
    return out.split()[-1] if out.split() else None


def runs(root, commit):
    """CI runs of the pull request behind a main commit, its head first. Just
    after a merge GitHub may not link the commit yet; the squash title's
    (#N) still names it."""
    pulls = gh(f'repos/{REPO}/commits/{commit}/pulls')
    if not pulls:
        number = re.search(r'\(#(\d+)\)$', run('git', '-C', str(root), 'log', '-1', '--format=%s', commit).strip())
        pulls = [gh(f'repos/{REPO}/pulls/{number.group(1)}')] if number else []
    found = []
    for pull in pulls[:1]:
        head, branch = pull['head']['sha'], pull['head']['ref']
        for query in (f'head_sha={head}', f'branch={branch}&per_page=30'):
            for row in gh(f'repos/{REPO}/actions/runs?{query}')['workflow_runs']:
                if row['id'] not in found:
                    found.append(row['id'])
    return found


def fetch(run_id, nevra, out, wanted):
    arch = nevra.rsplit('.', 1)[1]
    for artifact in gh(f'repos/{REPO}/actions/runs/{run_id}/artifacts')['artifacts']:
        name = artifact['name']
        if artifact['expired'] or (arch != 'noarch' and not name.endswith(arch)):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(['gh', 'run', 'download', str(run_id), '--repo', REPO, '-n', name, '-D', tmp],
                           check=True, capture_output=True)
            hits = [p for p in Path(tmp).rglob(f'{nevra}.rpm')]
            for rpm in Path(tmp).rglob('*.rpm'):
                # Other missing pins from the same build come along at no extra cost.
                if rpm.name[:-4] in wanted and not (out / rpm.name).exists():
                    rpm.replace(out / rpm.name)
            if hits:
                return name
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--have', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    have = {line.split()[0] for line in args.have.read_text().splitlines() if line.strip()}
    missing = sorted(pins(args.root, args.sha) - have)
    unresolved = []
    for nevra in missing:
        if (args.out / f'{nevra}.rpm').exists():
            print(f'have    {nevra} (fetched with a sibling)')
            continue
        commit = setter(args.root, args.sha, nevra)
        source = None
        for run_id in runs(args.root, commit) if commit else []:
            source = fetch(run_id, nevra, args.out, set(missing))
            if source:
                print(f'fetched {nevra} from run {run_id} ({source})')
                break
        if not source:
            unresolved.append(f'{nevra} (pinned by {commit[:8] if commit else "unknown"})')
    for line in unresolved:
        print(f'MISSING {line}')
    print(f'{len(missing)} pin(s) not in the pool, {len(unresolved)} unresolved')
    return 1 if unresolved else 0


if __name__ == '__main__':
    sys.exit(main())
