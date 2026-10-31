#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Bind actual ISO payload bodies to an externally pinned complete export index.

This creates donor body custody, never release or installation qualification.
"""
import argparse
import importlib.util
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--body-index', type=Path, required=True)
    parser.add_argument('--body-index-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    helper = Path(__file__).with_name('payload-object-reuse.py')
    spec = importlib.util.spec_from_file_location('payload_objects', helper)
    objects = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(objects)
    index = objects.load_pinned(args.body_index, args.body_index_sha256, objects.INDEX_SCHEMA)
    if index.get('ostree_full_fsck_verified') is not True:
        raise ValueError('payload body index lacks full OSTree verification')
    manifest = objects.manifest_from_index(args.repository, index)
    receipt = objects.verify_iso_inventory(args.iso, manifest)
    receipt['export_body_index_sha256'] = args.body_index_sha256
    receipt['body_verifier_sha256'] = objects.digest(helper)
    objects.save_private(args.output, receipt)
    print(objects.digest(args.output))


if __name__ == '__main__':
    main()
