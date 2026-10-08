#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Stage normal RPM/SRPM inputs from an observed native matching image pair."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing regular input: ' + str(path))
    return path


def copy(source, target):
    regular(source)
    digest = sha(source)
    shutil.copy2(source, target)
    if sha(target) != digest:
        raise ValueError('Source copy changed: ' + source.name)
    with target.open('rb') as stream:
        os.fsync(stream.fileno())
    return {'sha256': digest, 'bytes': target.stat().st_size}


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def stage(pair, member_path, output, repo, arch):
    if arch != 'x86_64':
        raise ValueError('No native current-frame pair qualified for this architecture; use the explicit reference builder for reference images')
    producer_path = regular(pair / 'RESULT.json')
    producer = json.loads(producer_path.read_text())
    members = json.loads(regular(member_path).read_text())
    identities = [producer[k] for k in ('source_manifest_sha256', 'source63_sha256') if k in producer]
    if len(identities) != 1 or not isinstance(identities[0], str) or not re.fullmatch('[a-f0-9]{64}', identities[0]):
        raise ValueError('Missing or ambiguous exact source identity')
    manifest = identities[0]
    invocation = producer['compiler_invocation_id']
    if (producer['result'] != 'CANONICAL-PAIR-RETAINED'
            or producer['actual_systemd_terminal_success'] is not True
            or type(producer['actual_container_exit_code']) is not int
            or producer['actual_container_exit_code'] != 0
            or not isinstance(invocation, str) or not re.fullmatch('[a-f0-9]{32}', invocation)
            or set(producer['images']) != {'system.img', 'vendor.img'}
            or set(producer['archives']) != {'system.img', 'vendor.img'}
            or members['result'] != 'PASS'
            or members['read_only_debugfs_no_filesystem_mount'] is not True
            or members.get('actual_Lineage_TaskStackListener_closeRemovedTask_and_HIDL13_native_java_VINTF_coherent') is not True
            or members['terminal_pair_receipt_sha256'] != sha(producer_path)):
        raise ValueError('No genuine terminal matching image/member proof')
    material_paths = list(pair.glob('*corresponding-materials.tar.gz'))
    if len(material_paths) != 1:
        raise ValueError('Expected one exact corresponding-material archive')
    materials = regular(material_paths[0])
    record = producer['source_material_archive']
    if sha(materials) != record['sha256'] or materials.stat().st_size != record['bytes']:
        raise ValueError('Corresponding materials changed')
    for record in list(producer['images'].values()) + list(producer['archives'].values()):
        if type(record['bytes']) is not int or record['bytes'] <= 0 or not re.fullmatch('[a-f0-9]{64}', record['sha256']):
            raise ValueError('Invalid exact image size or digest')
    budget = sum(r['bytes'] for r in producer['images'].values()) * 4 + sum(r['bytes'] for r in producer['archives'].values()) * 3 + 1024**3
    floor = 10 * 1024**3
    # This is a measured filesystem reserve, not sparse logical capacity.
    if shutil.disk_usage(output.parent).free < budget + floor:
        raise ValueError('Insufficient real canonical packaging scratch plus 10 GiB reserve')
    controls = {
        'SPECS/luma-android-images.spec': repo / 'packaging/rpm/luma-android-images.spec',
        'SOURCES/package-images.py': repo / 'scripts/android/package-images.py',
        'SOURCES/verify-packaged-image-provenance.py': repo / 'scripts/android/verify-packaged-image-provenance.py',
        'SOURCES/test_image_pair_admission.py': repo / 'tests/android/test_image_pair_admission.py',
        'SOURCES/ANDROID-IMAGES.md': repo / 'docs/research/android-native-image-package.md',
    }
    control_hashes = {str(p.relative_to(repo)): sha(regular(p)) for p in controls.values()}
    output.mkdir(mode=0o755)
    try:
        for name in ('BUILD', 'BUILDROOT', 'RPMS', 'SOURCES', 'SPECS', 'SRPMS'):
            (output / name).mkdir()
        records = {name: copy(source, output / name) for name, source in controls.items()}
        pins = {'LUMA_ANDROID_IMAGE_ORIGIN': 'luma-native-builder',
                'LUMA_ANDROID_SOURCE_MANIFEST_SHA256': manifest,
                'LUMA_ANDROID_COMPILER_INVOCATION_ID': invocation}
        for kind in ('system', 'vendor'):
            name = kind + '.img'
            archive = producer['archives'][name]
            source = regular(Path(archive['path']))
            if (source.parent != pair or not re.fullmatch(r'[A-Za-z0-9_.-]*waydroid_x86_64[A-Za-z0-9_.-]*\.zip', source.name)
                    or sha(source) != archive['sha256'] or source.stat().st_size != archive['bytes']):
                raise ValueError('Changed actual terminal archive')
            records['SOURCES/android-' + kind + '.zip'] = copy(source, output / 'SOURCES' / ('android-' + kind + '.zip'))
            prefix = 'LUMA_ANDROID_' + kind.upper() + '_'
            pins.update({prefix + 'FILENAME': source.name,
                         prefix + 'URL': 'private://native-pair/' + source.name,
                         prefix + 'SHA256': archive['sha256'], prefix + 'SIZE': str(archive['bytes']),
                         prefix + 'IMAGE_SHA256': producer['images'][name]['sha256'],
                         prefix + 'IMAGE_SIZE': str(producer['images'][name]['bytes'])})
        pin = output / 'SOURCES/images.env'
        pin.write_text(''.join(k + '=' + v + '\n' for k, v in pins.items()))
        records['SOURCES/images.env'] = {'sha256': sha(pin), 'bytes': pin.stat().st_size}
        for source, name in ((producer_path, 'TERMINAL-PRODUCER.json'), (member_path, 'IMAGE-MEMBERS.json'),
                             (materials, 'source-materials.tar.gz'), (pair / 'system-NOTICE.xml.gz', 'system-NOTICE.xml.gz'),
                             (pair / 'vendor-NOTICE.xml.gz', 'vendor-NOTICE.xml.gz'), (pair / 'Figtree-OFL.txt', 'Figtree-OFL.txt')):
            records['SOURCES/' + name] = copy(source, output / 'SOURCES' / name)
        for name, digest in control_hashes.items():
            if sha(repo / name) != digest:
                raise ValueError('Maintained controls changed during staging')
        save(output / 'STAGED-INPUTS.json', {
            'result': 'STAGED-NORMAL-CANONICAL-INPUTS',
            'observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'compiler_invocation_id': invocation, 'source_manifest_sha256': manifest,
            'maintained_controls': control_hashes,
            'terminal_producer_sha256': sha(producer_path), 'inside_image_members_sha256': sha(member_path),
            'actual_full_scratch_budget': budget, 'physical_reserve_bytes': floor, 'files': records,
            'normal_rpmbuild_complete': False, 'installed_acceptance_complete': False, 'public_source_offer_complete': False,
        })
        print('Genuine pair staged for full normal RPM/SRPM build', sha(output / 'STAGED-INPUTS.json'))
    except BaseException as error:
        save(output / 'ERROR.json', {'error': repr(error), 'partial_inputs_preserved': True, 'no_automatic_replay': True})
        raise


if __name__ == '__main__':
    if len(sys.argv) != 6 or not sys.dont_write_bytecode:
        raise SystemExit('usage: python -B -I stage-native-image-package.py PAIR MEMBERS OUTPUT REPO ARCH')
    stage(*(Path(p).resolve() for p in sys.argv[1:5]), sys.argv[5])
