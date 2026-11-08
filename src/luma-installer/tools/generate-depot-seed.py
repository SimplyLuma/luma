#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Export the Depot seed shipped in the package from the Hub's signed catalogue.

Depot listings are written in one place: the Hub (admin.simplyluma.com, Depot >
Apps). Nothing in this repository authors one. The build host signs the Hub's
snapshots (scripts/depot/sign-catalog.py) and publishes them at
https://dl.simplyluma.com/catalog/. The seed is what Depot shows before it has
ever verified a published catalogue, and what the installer reads its suites
from, so it is a copy of those signed documents:

* ``depot-catalog-4.json`` (+ ``.minisig``) -- the schema 4 catalogue;
* ``depot-catalog.json`` (+ ``.minisig``) -- its schema 3 subset (catalog-3.json).

Each document is verified against ``depot-catalog.pub`` and validated with the
same rules installed Depots run before it is written. ``--check`` verifies the
committed copies against their committed signatures, offline.

    generate-depot-seed.py                 # fetch the published catalogue
    generate-depot-seed.py --from DIR      # catalog-4.json(.minisig), catalog-3.json(.minisig)
    generate-depot-seed.py --check
"""

import argparse
import json
from pathlib import Path
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / 'data'
sys.path.insert(0, str(HERE.parent))

from luma_installer.depot_catalog import validate_catalog  # noqa: E402
from luma_installer.depot_signature import verify_file  # noqa: E402

BASE = 'https://dl.simplyluma.com/catalog/'
#: published name -> seed name
DOCUMENTS = {'catalog-4.json': 'depot-catalog-4.json', 'catalog-3.json': 'depot-catalog.json'}
LIMIT = 8 * 1024 * 1024


def verified(content, signature, key):
    """The parsed document once its signature and contents both hold."""
    verify_file(key, content, signature)
    document = json.loads(content)
    validate_catalog(document)
    return document


def _fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'luma-depot-seed-export'})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (https only)
        content = response.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise ValueError(f'{url} is larger than {LIMIT} bytes')
    return content


def export(source=None, data=DATA):
    key = (data / 'depot-catalog.pub').read_bytes()
    fetched = {}
    for published in DOCUMENTS:
        if source is None:
            fetched[published] = (_fetch(BASE + published), _fetch(BASE + published + '.minisig'))
        else:
            fetched[published] = ((Path(source) / published).read_bytes(),
                                  (Path(source) / (published + '.minisig')).read_bytes())
        verified(*fetched[published], key)
    for published, seed in DOCUMENTS.items():
        content, signature = fetched[published]
        (data / seed).write_bytes(content)
        (data / (seed + '.minisig')).write_bytes(signature)
    return list(DOCUMENTS.values())


def check(data=DATA):
    key = (data / 'depot-catalog.pub').read_bytes()
    problems = []
    for seed in DOCUMENTS.values():
        try:
            verified((data / seed).read_bytes(), (data / (seed + '.minisig')).read_bytes(), key)
        except Exception as error:  # noqa: BLE001 - every failure is a stale or edited seed
            problems.append(f'{seed}: {error}')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--from', dest='source', type=Path, help='a directory holding the signed documents')
    parser.add_argument('--check', action='store_true', help='verify the committed seed offline')
    options = parser.parse_args(argv)
    if options.check:
        problems = check()
        for line in problems:
            print(line, file=sys.stderr)
        if problems:
            print(f'the seed must be an unedited export of the signed catalogue; run {Path(__file__).name}',
                  file=sys.stderr)
        return 1 if problems else 0
    for name in export(options.source):
        print(f'wrote {DATA / name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
