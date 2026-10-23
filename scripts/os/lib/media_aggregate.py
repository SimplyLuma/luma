#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare the existing website's signed-index compatibility bridge.

No signing, remote writes, file deletion or release qualification happens here.
The operator must materialize and verify every planned alias before advertising
the prepared index, under its normal publication lock and prior-index CAS.
"""
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

KEY = 'RWRDS3jtjw9qdvQCeGSjoTFyrfbPMTjDcFHlKOy7Gr9Ot+nH0ASjW48c'
SCHEMA = 'org.projectluma.os-media-index/v1'
SHA = re.compile(r'[a-f0-9]{64}')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stamp(value):
    require(isinstance(value, str), 'Missing timestamp')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None, 'Timestamp requires timezone')
    return parsed


def digest(body):
    return hashlib.sha256(body).hexdigest()


def verified(path, comment, return_signature=False):
    """Verify the maintained issuer before interpreting a trusted comment."""
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'Expected regular signed input')
    with path.open('rb') as stream:
        body = stream.read(1024 * 1024 + 1)
    require(0 < len(body) <= 1024 * 1024, 'Signed JSON exceeds website bound')
    signature = Path(str(path) + '.minisig')
    require(signature.is_file() and not signature.is_symlink(), 'Missing regular signature')
    with signature.open('rb') as stream:
        signature_body = stream.read(4097)
    require(len(signature_body) <= 4096, 'Signature exceeds website bound')
    # Verify the exact captured bytes, even if a mutable download cache changes
    # its source path concurrently. No key material enters this verifier.
    with tempfile.TemporaryDirectory(prefix='luma-media-verify-') as directory:
        payload, proof = Path(directory) / 'payload', Path(directory) / 'signature'
        payload.write_bytes(body)
        proof.write_bytes(signature_body)
        subprocess.run(['minisign', '-Vm', str(payload), '-x', str(proof), '-P', KEY],
                       check=True, capture_output=True, timeout=30)
    lines = signature_body.decode().splitlines()
    if callable(comment):
        comment = comment(body)
    require(len(lines) == 4 and lines[2] == 'trusted comment: ' + comment,
            'Signature trusted comment differs from requested identity')
    return (body, signature_body) if return_signature else body


def entries(document, channel):
    require(document.get('schema') == SCHEMA and document.get('channel') == channel,
            'Wrong signed index schema/channel')
    stamp(document.get('generated_utc'))
    rows = document.get('entries')
    require(isinstance(rows, list) and len(rows) <= 400, 'Invalid index entries')
    result = {}
    for row in ([document['latest']] if document.get('latest') else []) + rows:
        arch = row.get('arch', 'x86_64')
        require(arch in ('x86_64', 'aarch64'), 'Unsupported architecture')
        build = row.get('build_id')
        require(isinstance(build, str) and len(build) <= 64 and NAME.fullmatch(build),
                'Unsafe build identity')
        require(row.get('channel') == channel, 'Entry channel differs')
        identity = (build, arch)
        require(identity not in result or result[identity] == row,
                'Conflicting duplicate build/architecture identity')
        result[identity] = copy.deepcopy(row)
    return result


def merge(canonical, incoming, channel, canonical_url, incoming_url, generated, notes):
    """Return unsigned index and alias plan from already verified inputs.

    notes(build_id, existing) returns exact verified JSON/signature bodies.
    Existing canonical entries are retained byte-for-byte as JSON values.
    """
    require(channel in ('nightly', 'beta', 'stable'), 'Invalid channel')
    target = urlsplit(canonical_url)
    source = urlsplit(incoming_url)
    require(target.scheme == source.scheme == 'https' and target.netloc == source.netloc
            and not target.username and not source.username and not target.query
            and not source.query and not target.fragment and not source.fragment,
            'Inputs must use the same credential-free HTTPS origin')
    require(target.path == f'/media/{channel}/latest.json', 'Wrong canonical index path')
    require(source.path == f'/media/aarch64/{channel}/latest.json', 'Wrong ARM index path')
    require(stamp(generated) > max(stamp(canonical['generated_utc']), stamp(incoming['generated_utc'])),
            'Aggregate generation must advance both verified inputs')
    original = entries(canonical, channel)
    additions = entries(incoming, channel)
    target_base, source_base = canonical_url.rsplit('/', 1)[0] + '/', incoming_url.rsplit('/', 1)[0] + '/'
    aliases, ignored = [], []
    result = copy.deepcopy(original)
    for identity, row in additions.items():
        require(identity[1] == 'aarch64', 'Scoped input contains another architecture')
        if row.get('public') is not True or row.get('interim') is True:
            ignored.append(list(identity))
            continue
        build, arch = identity
        filename = row.get('file', '')
        require(NAME.fullmatch(filename) and filename.endswith('.iso'), 'Invalid ISO filename')
        require(row.get('url') == source_base + filename, 'ISO is not beside scoped index')
        require(row.get('notes_url') == source_base + f'notes/{build}.json', 'Notes are not beside scoped index')
        require(SHA.fullmatch(row.get('sha256', '')), 'Invalid ISO hash')
        require(type(row.get('size_bytes')) is int and row['size_bytes'] > 0, 'Invalid ISO size')
        stamp(row.get('published_utc') or row.get('date'))
        note, signature = notes(build, False)
        # The website addresses notes by build_id alone. Never replace x86 notes
        # with architecture-specific content under the same existing address.
        if any(key[0] == build for key in original):
            prior_note, prior_signature = notes(build, True)
            require(note == prior_note, 'Same build ID has different signed release notes')
            # A fresh valid signature of identical notes is unnecessary: retain
            # the exact canonical pair already being served.
            signature = prior_signature
        else:
            for suffix, body in (('', note), ('.minisig', signature)):
                aliases.append({'kind': 'notes' + suffix, 'source_url': source_base + f'notes/{build}.json' + suffix,
                                'target_url': target_base + f'notes/{build}.json' + suffix,
                                'sha256': digest(body), 'bytes': len(body)})
        alias = filename if filename[:-4].endswith('-aarch64') else filename[:-4] + '-aarch64.iso'
        require(NAME.fullmatch(alias), 'Architecture alias exceeds website filename bound')
        row['file'], row['url'] = alias, target_base + alias
        row['notes_url'] = target_base + f'notes/{build}.json'
        row['sha256_url'] = target_base + alias + '.sha256'
        if row.get('iso_available') is not False:
            aliases.append({'kind': 'installer-iso', 'source_url': source_base + filename,
                            'target_url': row['url'], 'sha256': row['sha256'], 'bytes': row['size_bytes']})
            checksum = f"{row['sha256']}  {alias}\n".encode()
            aliases.append({'kind': 'checksum', 'target_url': row['sha256_url'],
                            'body_utf8': checksum.decode(), 'sha256': digest(checksum), 'bytes': len(checksum)})
        require(identity not in original or original[identity] == row,
                'Existing canonical entry would change')
        result[identity] = row
    require(len(result) <= 400, 'Aggregate exceeds website history bound; do not silently trim')
    rows = sorted(result.values(), key=lambda row: stamp(row.get('published_utc') or row['date']), reverse=True)
    require(rows, 'Cannot publish empty aggregate')
    # An alias must never collide with an existing ISO/notes address or another
    # alias carrying different bytes, including same-day multi-CPU releases.
    targets = {row['url']: (row.get('sha256'), row.get('size_bytes')) for row in original.values()}
    for alias in aliases:
        identity = (alias['sha256'], alias['bytes'])
        require(alias['target_url'] not in targets or targets[alias['target_url']] == identity,
                'Alias conflicts with an existing public object')
        targets[alias['target_url']] = identity
    aggregate = copy.deepcopy(canonical)
    aggregate.pop('arch', None)
    # Existing publishers differ on whether latest_url names the index or the
    # legacy latest ISO. Preserve that contract and the existing x86 alias.
    aggregate.update(generated_utc=generated, latest=rows[0], entries=rows)
    return aggregate, aliases, ignored


def check_prior(receipt, current_canonical_body):
    """Operator calls under the shared lock immediately before metadata write."""
    require(receipt.get('schema') == 'org.projectluma.media-aggregate-preparation/v1',
            'Wrong aggregate preparation receipt')
    require(digest(current_canonical_body) == receipt.get('canonical_prior_sha256'),
            'Canonical index changed; prepare again instead of replacing another publication')


def prepare(args):
    old_body = verified(args.canonical, lambda body:
                        f"luma-media-index channel={args.channel} generated_utc={json.loads(body)['generated_utc']}")
    new_body = verified(args.incoming, lambda body:
                        f"luma-media-index channel={args.channel} arch=aarch64 generated_utc={json.loads(body)['generated_utc']}")
    canonical, incoming = json.loads(old_body), json.loads(new_body)

    def notes(build, existing):
        directory = args.canonical.parent if existing else args.incoming.parent
        path = directory / 'notes' / (build + '.json')
        body, signature = verified(path, lambda body:
                        f"luma-release-notes channel={args.channel} build_id={build} generated_utc={json.loads(body)['generated_utc']}",
                        return_signature=True)
        doc = json.loads(body)
        require(doc.get('channel') == args.channel and doc.get('build_id') == build,
                'Signed notes identity differs')
        stamp(doc.get('generated_utc'))
        return body, signature

    aggregate, aliases, ignored = merge(canonical, incoming, args.channel, args.canonical_url,
                                         args.incoming_url, args.generated_utc, notes)
    body = (json.dumps(aggregate, indent=2, sort_keys=True) + '\n').encode()
    require(len(body) <= 1024 * 1024, 'Aggregate exceeds website JSON byte bound')
    args.output.mkdir(mode=0o700)
    (args.output / 'latest.json').write_bytes(body)
    receipt = {'status': 'PREPARED_UNSIGNED_ALIASES_PENDING', 'schema': 'org.projectluma.media-aggregate-preparation/v1',
               'canonical_url': args.canonical_url, 'canonical_prior_sha256': digest(old_body),
               'incoming_url': args.incoming_url, 'incoming_sha256': digest(new_body),
               'aggregate_sha256': digest(body), 'ignored_nonpublic': ignored, 'aliases': aliases,
               'trusted_comment': f'luma-media-index channel={args.channel} generated_utc={args.generated_utc}',
               'publication_requirements': ['materialize and verify all exact aliases', 'sign and verify prepared index',
                                             'reacquire normal publication lock', 'compare current canonical bytes with canonical_prior_sha256 before mutation',
                                             'publish metadata last and read back signature/website routes']}
    (args.output / 'PREPARATION.json').write_text(json.dumps(receipt, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', required=True)
    parser.add_argument('--canonical', required=True, type=Path)
    parser.add_argument('--incoming', required=True, type=Path)
    parser.add_argument('--canonical-url', required=True)
    parser.add_argument('--incoming-url', required=True)
    parser.add_argument('--generated-utc', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--lock-file', required=True, type=Path)
    args = parser.parse_args()
    with args.lock_file.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prepare(args)


if __name__ == '__main__':
    main()
