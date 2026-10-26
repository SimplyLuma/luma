#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Keyless, streaming full-body readback of an exact prepared ISO URL set."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.request

SCHEMA = 'org.projectluma.public-media-readback/v1'
ORIGIN = 'https://media.simplyluma.com'


def digest(body):
    return hashlib.sha256(body).hexdigest()


def validate_profile(value):
    assert set(value) == {'schema', 'build_id', 'architecture', 'source_revision', 'candidate',
                          'prepared_sha256', 'aggregate_sha256', 'iso', 'targets'}
    assert value['schema'] == SCHEMA and value['architecture'] == 'aarch64'
    assert re.fullmatch(r'[0-9]{8}\.[0-9]+', value['build_id'])
    assert re.fullmatch('[a-f0-9]{40}', value['source_revision'])
    for key in ('candidate', 'prepared_sha256', 'aggregate_sha256'):
        assert re.fullmatch('[a-f0-9]{64}', value[key])
    assert set(value['iso']) == {'bytes', 'sha256'}
    assert type(value['iso']['bytes']) is int and 0 < value['iso']['bytes'] <= 32*2**30
    assert re.fullmatch('[a-f0-9]{64}', value['iso']['sha256'])
    assert 2 <= len(value['targets']) <= 3
    urls = set()
    for target in value['targets']:
        assert set(target) == {'url', 'filename'}
        assert re.fullmatch(r'[A-Za-z0-9_.-]+\.iso', target['filename'])
        assert value['build_id'] in target['filename']
        assert target['url'] in (ORIGIN+'/media/nightly/'+target['filename'],
                                 ORIGIN+'/media/aarch64/nightly/'+target['filename'])
        assert target['url'] not in urls
        urls.add(target['url'])
    assert sum('/media/aarch64/nightly/' in url for url in urls) == 1
    return value


def read_iso(target, expected, opener=urllib.request.urlopen, clock=time.monotonic):
    request = urllib.request.Request(target['url'], headers={
        'Cache-Control': 'no-cache', 'User-Agent': 'Luma-keyless-media-readback/1'})
    started = clock()
    count = 0
    value = hashlib.sha256()
    with opener(request, timeout=120) as response:
        assert response.status == 200 and response.geturl() == target['url']
        assert response.headers.get_content_type() == 'application/x-iso9660-image'
        assert response.headers.get_filename() == target['filename']
        if response.headers.get('Content-Length') is not None:
            assert int(response.headers['Content-Length']) == expected['bytes']
        headers = dict(response.headers.items())
        for chunk in iter(lambda: response.read(8*2**20), b''):
            assert clock()-started <= 1800, 'Whole ISO readback exceeded bounded 30 minutes'
            count += len(chunk)
            assert count <= expected['bytes']
            value.update(chunk)
    assert (count, value.hexdigest()) == (expected['bytes'], expected['sha256'])
    return {'url': target['url'], 'resolved_url': response.geturl(), 'filename': target['filename'],
            'status': response.status, 'headers': headers, 'bytes': count, 'sha256': value.hexdigest(),
            'full_body_verified': True, 'observed_utc': dt.datetime.now(dt.timezone.utc).isoformat()}


def write(path, body):
    with path.open('xb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-json-env', required=True)
    parser.add_argument('--profile-sha256', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    body = os.environ[args.profile_json_env].encode()
    assert len(body) <= 16384 and re.fullmatch('[a-f0-9]{64}', args.profile_sha256)
    assert digest(body) == args.profile_sha256
    profile = validate_profile(json.loads(body))
    assert args.out.is_absolute() and not args.out.exists()
    args.out.mkdir(mode=0o700)
    write(args.out/'PROFILE.json', body)
    assets = []
    for index, target in enumerate(profile['targets']):
        receipt = read_iso(target, profile['iso'])
        write(args.out/f'ISO-READBACK-{index}.json', (json.dumps(receipt, sort_keys=True)+'\n').encode())
        assets.append(receipt)
    receipt = {'result': 'PASS_EXACT_FULL_PUBLIC_ISO_URL_SET', 'profile_sha256': digest(body),
               'assets': assets, 'publication_performed': False}
    write(args.out/'PASS.json', (json.dumps(receipt, sort_keys=True)+'\n').encode())
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
