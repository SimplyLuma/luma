#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Admit explicit desktop-only qualification without claiming another install.

The release operator seals actual producer receipts. This validates their
identity and the package boundary; it does not manufacture guest results.
Ordinary full qualification remains the default in publish-channel.sh.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

SCHEMA = 'org.projectluma.targeted-desktop-gate/v1'
CHANGED = frozenset(('gnome-shell', 'gnome-shell-common',
                     'luma-developer-platform', 'luma-developer-platform-sdk',
                     'prairie-icon-theme'))
CHECKS = frozenset(('update', 'var-data-after-update', 'manual-rollback',
                    'var-data-after-rollback', 'return-to-candidate',
                    'no-account-shell', 'desktop-startup',
                    'native-frame-live-mode', 'shell-surfaces', 'wifi-icons'))
PROOFS = frozenset(('prior-manifest', 'prior-gate', 'prior-provenance',
                   'candidate-provenance', 'prior-packages', 'candidate-packages',
                   'guest', 'media-boot', 'changed-components', 'runtime-reuse',
                   'app-export'))


def require(value, message):
    if not value:
        raise ValueError(message)


def protected(path, owner_uid):
    path = Path(path)
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            'Proof must be an absolute path without symlink traversal')
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == owner_uid and
                not before.st_mode & 0o222 and before.st_size < 16 * 1024**2,
                'Proof must be a bounded, sealed file owned by the release operator')
        body = stream.read(16 * 1024**2)
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'Proof changed while reading')
    return body


def packages(body):
    rows = [line.split('\t') for line in body.decode().splitlines() if line.strip()]
    require(all(len(row) == 8 for row in rows), 'Complete eight-column RPM inventory required')
    result = {(row[0], row[4]): row for row in rows if row[0] != 'gpg-pubkey'}
    require(len(result) == sum(row[0] != 'gpg-pubkey' for row in rows), 'Duplicate package identity')
    return result


def validate(gate, candidate, head, architecture, provenance_sha256, *, owner_uid=0):
    require(gate.get('schema') == SCHEMA and gate.get('result') == 'pass', 'Explicit targeted schema required')
    require(re.fullmatch('[0-9a-f]{64}', candidate) and candidate != head and
            (gate.get('commit'), gate.get('previous_commit'), gate.get('architecture')) ==
            (candidate, head, architecture), 'Targeted candidate/previous/architecture mismatch')
    require(architecture in ('x86_64', 'aarch64') and gate.get('channel') == 'nightly',
            'Targeted qualification is desktop Nightly only')
    require(gate.get('fresh') == 'reused' and gate.get('automatic_rollback') in ('pass', 'reused') and
            all(gate.get(k) == 'pass' for k in ('no_account', 'update', 'rollback')),
            'Record installation/recovery reuse and actual new boot/update/manual rollback separately')
    require(set(gate.get('proofs', {})) == PROOFS, 'Complete targeted proof inventory required')
    bodies = {}
    for name, row in gate['proofs'].items():
        body = protected(row['path'], owner_uid)
        require(hashlib.sha256(body).hexdigest() == row['sha256'], 'Changed proof: ' + name)
        bodies[name] = body
    docs = {name: json.loads(body) for name, body in bodies.items() if not name.endswith('packages')}
    prior, old, new = docs['prior-manifest'], docs['prior-provenance'], docs['candidate-provenance']
    require(prior.get('commit') == head and prior['gate']['result'] == 'pass' and
            prior['gate']['sha256'] == gate['proofs']['prior-gate']['sha256'] and
            prior['provenance_sha256'] == gate['proofs']['prior-provenance']['sha256'],
            'Reused evidence must belong to the previously published release')
    require(gate['proofs']['candidate-provenance']['sha256'] == provenance_sha256 and
            new['source'] == {'revision': gate['source_revision'], 'dirty': False} and
            new['architecture'] == old['architecture'] == architecture and
            new['build_id'] == gate['build_id'] and new['channel'] == old['channel'] == 'nightly' and
            new['recipe']['path'] == old['recipe']['path'] and new['base_image'] == old['base_image'],
            'Recipe, base image or candidate provenance changed outside the desktop boundary')
    old_gate = docs['prior-gate']
    require(old_gate.get('commit') == head and
            all(old_gate.get(k) == 'pass' for k in ('result', 'fresh', 'no_account')),
            'Prior full fresh/no-account installation evidence required')
    first_release = (prior.get('parent') is None and not old_gate.get('previous_commit') and
                     all(old_gate.get(k) == 'not-applicable' for k in ('update', 'rollback')))
    require(first_release or all(old_gate.get(k) == 'pass' for k in ('update', 'rollback')),
            'Previous update/recovery may be absent only on a first release')
    if gate['automatic_rollback'] == 'reused':
        require(not first_release and any(s.get('stage') == 'rollback-automatic' and s.get('result') == 'pass'
                for s in old_gate.get('stages', [])), 'Automatic recovery cannot reuse an unrun prior check')
    for label, provenance in (('prior', old), ('candidate', new)):
        require(provenance['installed_packages']['inventory_sha256'] == gate['proofs'][label+'-packages']['sha256'],
                'RPM inventory is not bound to provenance')
    a, b = (packages(bodies[k]) for k in ('prior-packages', 'candidate-packages'))
    changed = {key[0] for key in a.keys() | b.keys() if a.get(key) != b.get(key)}
    require(changed and changed <= CHANGED and a.keys() == b.keys(),
            'Changes to installer, kernel, updater, boot recovery or another package require full qualification')
    old_luma, new_luma = old['luma_packages']['packages'], new['luma_packages']['packages']
    def unchanged(rows):
        return sorted((row for row in rows if not any(row['nevra'].startswith(n+'-')
                       for n in CHANGED)), key=lambda row: row['nevra'])
    require(unchanged(old_luma) == unchanged(new_luma), 'Unrelated Luma RPM body/header changed')
    for name in ('guest', 'media-boot', 'changed-components', 'runtime-reuse', 'app-export'):
        doc = docs[name]
        require(doc.get('result') == 'PASS' and
                (doc.get('candidate'), doc.get('source_revision'), doc.get('architecture')) ==
                (candidate, gate['source_revision'], architecture), 'Wrong or failed producer proof: '+name)
    guest = docs['guest']
    checks = guest.get('checks', [])
    require(guest.get('previous_commit') == head and CHECKS <= {r['name'] for r in checks}
            and all(r.get('result') == 'PASS' for r in checks), 'Missing or failed new candidate guest checks')
    if gate['automatic_rollback'] == 'pass':
        require(any(r.get('name') == 'automatic-rollback' and r.get('result') == 'PASS' for r in checks),
                'Actual automatic recovery check required')
    require(docs['runtime-reuse'].get('nonclock_runtime_files_identical') is True and
            docs['runtime-reuse'].get('critical_source_inputs_identical') is True,
            'Installer runtime/source reuse requires exact input equality')
    media = docs['media-boot']
    require(re.fullmatch('[0-9a-f]{64}', media.get('iso_sha256', '')) and
            media.get('iso_bytes', 0) > 0 and media.get('actual_iso_boot') is True,
            'Actual final ISO boot required')
    return {'result': 'PASS', 'scope': 'targeted-desktop', 'reused': ['fresh-install'] +
            (['automatic-rollback'] if gate['automatic_rollback'] == 'reused' else []),
            'changed_packages': sorted(changed), 'proofs': gate['proofs']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ('gate', 'candidate', 'previous', 'architecture', 'provenance-sha256'):
        p.add_argument('--'+arg, required=True)
    a = p.parse_args()
    print(json.dumps(validate(json.loads(Path(a.gate).read_bytes()), a.candidate,
                             a.previous, a.architecture, a.provenance_sha256), indent=2))


if __name__ == '__main__':
    main()
