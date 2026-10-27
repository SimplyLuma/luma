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


class AdditionalComponentAdmission(unittest.TestCase):
    """Synthetic policy controls, never release/Windows qualification."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.record = dict(architecture='x86_64', build_id='20261027.4',
                           source_revision='eccd6bb20f2b2fd0afb784ea868621aabc09ca75',
                           commit='b'*64, previous_commit='a'*64,
                           additional_component_policy='fedora-wine-20261027')
        rows = []
        self.old, self.new = {}, {}
        for number in range(28):
            name = 'wine-fixture-' + str(number)
            old = [name, '0', '1', '1', 'x86_64', 'old-header', 'wine.src.rpm', 'LGPL']
            new = old[:]
            new[2], new[5] = '2', 'new-header'
            self.old[(name,'x86_64')], self.new[(name,'x86_64')] = old,new
            rows.append(dict(name=name, architecture='x86_64', old=old, new=new))
        self.bodies = {label: ''.join('\t'.join(row)+'\n' for row in sorted(inventory.values())).encode()
                       for label,inventory in (('prior-packages',self.old),('candidate-packages',self.new))}
        delta = dict(result='PASS_EXACT_28_UNPLANNED_FEDORA_WINE_PACKAGE_DELTAS_AUTHENTICATED_NOT_FUNCTIONAL_QUALIFICATION',
                     build_id=self.record['build_id'], architecture='x86_64',
                     source_revision=self.record['source_revision'], previous=self.record['previous_commit'],
                     normal_fedora_gpgcheck_enabled=True, signature_skip_only_private_luma_repository=True,
                     prior_inventory_sha256=hashlib.sha256(self.bodies['prior-packages']).hexdigest(),
                     candidate_inventory_sha256=hashlib.sha256(self.bodies['candidate-packages']).hexdigest(),
                     deltas=rows)
        self.component = {'fedora_wine_delta':self.seal('delta',delta)}
        self.smoke = dict(result='PASS', candidate=self.record['commit'], previous_commit=self.record['previous_commit'],
                          source_revision=self.record['source_revision'], architecture='x86_64',
                          checks=[dict(name=name,result='PASS') for name in ('wine-prefix-initialization','wine-pe-console')],
                          console_stdout='LUMA_WINE_NIGHTLY_20261027\n', prefix_exit_status=0, console_exit_status=0)
        self.component['wine_smoke'] = self.seal('smoke',self.smoke)
        original = gate.APPROVED_FEDORA_WINE_DELTA_SHA256
        self.addCleanup(setattr,gate,'APPROVED_FEDORA_WINE_DELTA_SHA256',original)
        gate.APPROVED_FEDORA_WINE_DELTA_SHA256 = self.component['fedora_wine_delta']['sha256']

    def seal(self,name,doc):
        path = self.root/name
        if path.exists(): path.chmod(0o600)
        raw = json.dumps(doc).encode()
        path.write_bytes(raw)
        path.chmod(0o400)
        return dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())

    def validate(self):
        return gate.additional_component_names(self.record,self.bodies,
                                               {'changed-components':self.component},
                                               self.old,self.new,os.getuid())

    def test_exact_reviewed_rows_and_actual_smoke_required(self):
        self.assertEqual(len(self.validate()),28)

    def test_reject_new_kernel_even_with_component_policy(self):
        self.new[('kernel','x86_64')] = ['kernel','0','2','1','x86_64','header','kernel.src.rpm','GPL']
        with self.assertRaisesRegex(ValueError,'Unreviewed additional RPM'): self.validate()

    def test_reject_future_build_or_other_architecture(self):
        for field,value in (('build_id','20261028.1'),('architecture','aarch64')):
            with self.subTest(field=field):
                previous = self.record[field]
                self.record[field] = value
                with self.assertRaises(ValueError): self.validate()
                self.record[field] = previous

    def test_reject_changed_inventory(self):
        self.bodies['candidate-packages'] += b'changed\n'
        with self.assertRaisesRegex(ValueError,'different inventories'): self.validate()

    def test_reject_changed_or_unsigned_authentication_receipt(self):
        self.component['fedora_wine_delta']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError,'Changed component evidence'): self.validate()

    def test_reject_failed_console_and_stale_candidate(self):
        for field,value in (('console_exit_status',1),('candidate','c'*64),('console_stdout','')):
            with self.subTest(field=field):
                doc = self.smoke | {field:value}
                self.component['wine_smoke'] = self.seal('smoke',doc)
                with self.assertRaises(ValueError): self.validate()

    def test_reject_missing_functional_check(self):
        self.component['wine_smoke'] = self.seal('smoke',self.smoke | {'checks':[]})
        with self.assertRaisesRegex(ValueError,'Actual new Wine checks'): self.validate()


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
