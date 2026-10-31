# SPDX-License-Identifier: Apache-2.0
"""Project public identity expectations without packaging private listing data."""
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
    source = data / 'depot-first-party.json'
    if not source.is_file():
        fixture = data.parent / 'tests/fixtures/depot-public-first-party.json'
        return checked_ids(json.loads(fixture.read_text())['first_party_ids'])
    document = json.loads(source.read_text())
    channels = json.loads((data / 'depot-channels.json').read_text())['applications']
    result = []
    for row in document['applications']:
        entry = {**document.get('defaults', {}), **row}
        visibility = channels.get(entry['id'], {}).get('visibility', entry.get('visibility'))
        if visibility in ('public', 'unlisted'):
            result.append(entry['id'])
    return checked_ids(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(json.dumps({'first_party_ids': public_ids(arguments.data)},
                                          indent=2) + '\n')
