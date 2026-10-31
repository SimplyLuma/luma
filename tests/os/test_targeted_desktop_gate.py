#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic admission controls; these fixtures do not qualify a release."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('targeted', ROOT/'scripts/os/lib/targeted_desktop_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class TargetedAdmission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old, self.new = 'a'*64, 'b'*64
        self.record = dict(schema=gate.SCHEMA, result='pass', commit=self.new,
                           previous_commit=self.old, architecture='aarch64', channel='nightly',
                           source_revision='c'*40, build_id='20261027.2', fresh='reused',
                           automatic_rollback='reused', no_account='pass', update='pass',
                           rollback='pass', proofs={})
        self.docs = {}
        for label, release in (('prior', '1'), ('candidate', '2')):
            body = ('kernel\t0\t1\t1\taarch64\tunchanged-header\tkernel.src.rpm\tGPL\n'
                    f'gnome-shell\t0\t1\t{release}\taarch64\theader-{release}\tshell.src.rpm\tGPL\n').encode()
            self.seal(label+'-packages', body)
            p = dict(source={'revision': 'c'*40, 'dirty': False}, architecture='aarch64',
                     build_id='20261027.2', channel='nightly', recipe={'path': 'Containerfile', 'digest': release},
                     base_image={'digest': 'fixed'}, luma_packages={'packages': []},
                     installed_packages={'inventory_sha256': hashlib.sha256(body).hexdigest()})
            self.seal(label+'-provenance', p)
        self.seal('prior-gate', dict(commit=self.old, result='pass', fresh='pass', no_account='pass',
                  update='pass', rollback='pass', previous_commit='d'*64,
                  stages=[dict(stage='rollback-automatic', result='pass')]))
        self.seal('prior-manifest', dict(commit=self.old, parent='d'*64,
                  gate={'result': 'pass', 'sha256': self.record['proofs']['prior-gate']['sha256']},
                  provenance_sha256=self.record['proofs']['prior-provenance']['sha256']))
        for name in ('guest', 'media-boot', 'changed-components', 'runtime-reuse', 'app-export'):
            self.seal(name, dict(result='PASS', candidate=self.new, source_revision='c'*40,
                                architecture='aarch64'))
        self.update('guest', previous_commit=self.old,
                    checks=[dict(name=k, result='PASS') for k in gate.CHECKS])
        self.update('runtime-reuse', nonclock_runtime_files_identical=True, critical_source_inputs_identical=True)
        self.update('media-boot', iso_sha256='e'*64, iso_bytes=100, actual_iso_boot=True)

    def seal(self, name, doc):
        body = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
        path = self.root/name
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(body)
        path.chmod(0o400)
        self.docs[name] = doc
        self.record['proofs'][name] = dict(path=str(path), sha256=hashlib.sha256(body).hexdigest())

    def update(self, name, **fields):
        doc = copy.deepcopy(self.docs[name])
        doc.update(fields)
        self.seal(name, doc)

    def validate(self):
        return gate.validate(self.record, self.new, self.old, 'aarch64',
                             self.record['proofs']['candidate-provenance']['sha256'], owner_uid=os.getuid())

    def test_explicit_reuse_keeps_actual_checks(self):
        self.assertEqual(self.validate()['reused'], ['fresh-install', 'automatic-rollback'])

    def test_reject_wrong_arch_or_candidate(self):
        for field, value in (('architecture', 'x86_64'), ('commit', 'd'*64), ('previous_commit', 'd'*64)):
            with self.subTest(field=field):
                old = self.record[field]
                self.record[field] = value
                with self.assertRaises(ValueError): self.validate()
                self.record[field] = old

    def test_changed_kernel_is_not_desktop_only(self):
        body = self.docs['candidate-packages'].replace(b'unchanged-header', b'different-header')
        self.seal('candidate-packages', body)
        self.update('candidate-provenance', installed_packages={'inventory_sha256': hashlib.sha256(body).hexdigest()})
        with self.assertRaisesRegex(ValueError, 'require full qualification'): self.validate()

    def test_missing_failed_and_stale_guest_checks(self):
        for checks in ([], [dict(name=k, result='FAIL') for k in gate.CHECKS]):
            self.update('guest', checks=checks)
            with self.assertRaises(ValueError): self.validate()
        self.update('guest', candidate=self.old)
        with self.assertRaises(ValueError): self.validate()

    def test_proof_is_digest_bound_sealed_and_not_symlinked(self):
        path = Path(self.record['proofs']['guest']['path'])
        path.chmod(0o600)
        with self.assertRaises(ValueError): self.validate()
        path.chmod(0o400)
        self.record['proofs']['guest']['sha256'] = 'f'*64
        with self.assertRaises(ValueError): self.validate()

    def test_first_release_needs_actual_new_automatic_recovery(self):
        self.update('prior-gate', previous_commit=None, update='not-applicable', rollback='not-applicable', stages=[])
        self.update('prior-manifest', parent=None,
                    gate={'result': 'pass', 'sha256': self.record['proofs']['prior-gate']['sha256']})
        with self.assertRaisesRegex(ValueError, 'cannot reuse'): self.validate()
        self.record['automatic_rollback'] = 'pass'
        with self.assertRaisesRegex(ValueError, 'Actual automatic'): self.validate()
        self.update('guest', checks=self.docs['guest']['checks']+[dict(name='automatic-rollback', result='PASS')])
        self.assertEqual(self.validate()['reused'], ['fresh-install'])


if __name__ == '__main__': unittest.main()
