# SPDX-License-Identifier: Apache-2.0
"""Project public identity expectations without packaging private listing data.

Luma's public first-party listings are the seed's ``luma`` tier entries (the
seed is an export of the Hub's signed catalogue). A packaged copy keeps only
these ids in tests/fixtures/depot-public-first-party.json.
"""
import argparse
import json
from pathlib import Path


def checked_ids(result):
    if (not isinstance(result, list) or not result or
            any(not isinstance(value, str) or not value for value in result) or
            len(result) != len(set(result))):
        raise ValueError('Public first-party identities must be nonempty and unique')
    return sorted(result)


def public_ids(data):
    data = Path(data)
    seed = data / 'depot-catalog-4.json'
    if not seed.is_file():
        fixture = data.parent / 'tests/fixtures/depot-public-first-party.json'
        return checked_ids(json.loads(fixture.read_text())['first_party_ids'])
    document = json.loads(seed.read_text())
    return checked_ids([row['id'] for row in document['applications']
                        if row.get('tier') == 'luma' and row.get('visibility') in ('public', 'unlisted')])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(json.dumps({'first_party_ids': public_ids(arguments.data)},
                                          indent=2) + '\n')
