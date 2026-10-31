#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Publication-facing metadata from a fully closed installed server fixture.

This adapter does not publish, create evidence, or qualify a historical Mac run.
Callers must independently pin the final release's expected inputs, and retain
all existing OS/media/static/signing/public-readback guards around this result.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

# Load the maintained validator by its exact sibling path, not PYTHONPATH or a
# preloaded module supplied by the publication process.
_spec = importlib.util.spec_from_file_location(
    '_installed_contract_server_provider',
    Path(__file__).resolve().with_name('server_installed_qualification.py'))
provider = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(provider)


def server_publication_contract(fixture: Path, expected: dict, *, owner_uid=0) -> dict:
    """Validate the entire producer chain and return explicit TCG metadata.

    No default build/source/candidate/ISO or expected inventory is permitted.
    owner_uid is root in the release infrastructure; non-root is for isolated
    synthetic unit fixtures only. This function never writes to the fixture.
    """
    provider.require(isinstance(expected, dict), 'Independent release inputs required')
    provider.validate_expected(expected)
    checked = provider.validate_server_qualification(fixture, expected, owner_uid=owner_uid)
    provider.require(checked['provider'] == provider.PROVIDER and
                     checked['architecture'] == 'aarch64' and
                     checked['physical_hardware_coverage'] is False,
                     'Explicit ARM server TCG coverage required')
    fixture = Path(fixture)
    qualified = checked['qualified_metadata']
    # Use the actual guest result body validated in the receipt chain, rather
    # than trusting a duplicate results array in a caller-created summary.
    guest_path = fixture / 'guest-result.json'
    guest_body = provider.regular_bytes(guest_path, owner_uid=owner_uid, immutable=True)
    guest_sha = hashlib.sha256(guest_body).hexdigest()
    provider.require(checked['proof_hashes'].get(str(guest_path)) == guest_sha and
                     qualified['proof_hashes'].get('guest-result.json') == guest_sha,
                     'Guest result must belong to the closed proof inventory')
    guest = json.loads(guest_body)
    return {
        'schema': 'org.projectluma.installed-publication-contract/v1',
        'result': 'PASS',
        'qualification_provider': checked['provider'],
        'execution_backend': 'qemu-tcg',
        'architecture': 'aarch64',
        'profile': expected['profile'],
        'physical_hardware_coverage': False,
        'build_id': expected['build_id'],
        'source_revision': expected['source_revision'],
        'candidate': expected['candidate'],
        'exact_iso_sha256': expected['iso_sha256'],
        'exact_iso_bytes': expected['iso_bytes'],
        'installation_fixture': fixture.name,
        'fixture_token': expected['fixture_token'],
        'closed_receipt_sha256': checked['closed_receipt_sha256'],
        'guest_result_sha256': guest_sha,
        'results': guest['results'],
        'proof_hashes': checked['proof_hashes'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', type=Path)
    parser.add_argument('--expected', required=True, type=Path)
    args = parser.parse_args()
    expected = json.loads(provider.regular_bytes(args.expected, owner_uid=0, immutable=True,
                                               maximum_bytes=4 * 1024**2))
    print(json.dumps(server_publication_contract(args.fixture, expected), indent=2))


if __name__ == '__main__':
    main()
