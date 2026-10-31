#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Synthetic receipt-chain tests; no actual guest/release qualification claimed."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

M = load('installed_publication_contract', ROOT / 'scripts/os/lib/installed_qualification_contract.py')
FIXTURES = load('server_contract_fixtures', ROOT / 'tests/os/test_server_installed_qualification.py')


class PublicationContractTests(unittest.TestCase):
    def setUp(self):
        self.f = FIXTURES.ServerQualification()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def validate(self, expected=None):
        return M.server_publication_contract(self.f.fixture,
                                             self.f.expected if expected is None else expected,
                                             owner_uid=os.getuid())

    def test_complete_synthetic_chain_yields_explicit_tcg_never_mac(self):
        before = {p.name: p.read_bytes() for p in self.f.fixture.iterdir() if p.is_file()}
        result = self.validate()
        self.assertEqual(result['execution_backend'], 'qemu-tcg')
        self.assertFalse(result['physical_hardware_coverage'])
        self.assertEqual(result['source_revision'], self.f.expected['source_revision'])
        self.assertEqual(result['exact_iso_sha256'], self.f.expected['iso_sha256'])
        self.assertEqual(len(result['results']), 11)
        self.assertFalse(set(result) & M.provider.MAC_KEYS)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.f.fixture.iterdir() if p.is_file()})

    def test_guest_body_not_unvalidated_duplicate_summary_supplies_rows(self):
        self.f.mutate('QUALIFIED.json', lambda q: q.update(results=[{'invented': True}]))
        self.assertEqual(len(self.validate()['results']), 11)

    def test_no_independent_inputs_or_placeholder_defaults(self):
        for expected in (None, {}, {**self.f.expected, 'candidate': 'PENDING'}):
            with self.subTest(expected=expected), self.assertRaises((ValueError, TypeError)):
                M.server_publication_contract(self.f.fixture, expected, owner_uid=os.getuid())

    def test_changed_source_candidate_iso_or_baseline_binding_refused(self):
        for field in ('source_revision', 'candidate', 'iso_sha256', 'app_baseline_sha256'):
            expected = copy.deepcopy(self.f.expected)
            expected[field] = 'f' * len(expected[field])
            with self.subTest(field=field), self.assertRaises(ValueError): self.validate(expected)

    def test_missing_closed_receipt_refused(self):
        (self.f.fixture / 'CLOSED.json').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)): self.validate()

    def test_changed_proof_bytes_refused(self):
        path = self.f.fixture / 'guest-result.json'
        path.chmod(0o600)
        path.write_text('{}')
        path.chmod(0o400)
        with self.assertRaises(ValueError): self.validate()

    def test_missing_behavior_refused_even_with_resealed_chain(self):
        self.f.proof_mutation('guest-result.json', 'QUALIFIED', lambda q: q['results'].pop())
        with self.assertRaises(ValueError): self.validate()

    def test_historical_mac_fields_refused(self):
        self.f.mutate('QUALIFIED.json', lambda q: q.update(native_mac_vm='historical'))
        with self.assertRaises(ValueError): self.validate()

    def test_unsigned_modification_refused(self):
        self.f.mutate('INSTALLED.json', lambda q: q.update(unsigned_modifications=['mechanism-overlay']))
        with self.assertRaises(ValueError): self.validate()

    def test_normal_nonzero_refused_even_with_resealed_chain(self):
        self.f.proof_mutation('normal-wrapper-outcome.json', 'CLOSED', lambda q: q.update(exit_status=1))
        with self.assertRaises(ValueError): self.validate()

    def test_changed_archive_body_refused(self):
        self.f.archive.write_bytes(b'not the archived disk')
        with self.assertRaises(ValueError): self.validate()


if __name__ == '__main__': unittest.main()
