#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Exercise Depot's signed app removal without removing its native engine.

Run only on the fresh disposable OS gate. Restore the authenticated offline
bundle through the existing luma remote, never through a new bundle origin or
another baseline handoff. The completion marker and user data must survive.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import platform
import pwd
import runpy
import shlex
import subprocess
import tempfile

import gi
gi.require_version('Flatpak', '1.0')
from gi.repository import Flatpak, Gio
from luma_installer.depot_flatpak import removal_transaction, validate_remote
from luma_installer.native_app_roles import installed, required, verify_deployed_commit

APP = 'org.projectluma.Leaf'
BASELINE = Path('/usr/share/luma/app-baseline')
MARKER = Path('/var/lib/luma/app-baseline/installed-v1.json')


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=180).stdout


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fingerprint(path):
    st = path.lstat()
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'device': st.st_dev, 'inode': st.st_ino, 'mtime_ns': st.st_mtime_ns,
            'mode': st.st_mode, 'uid': st.st_uid, 'bytes': st.st_size}


def readability_account(pre_account=False):
    if pre_account:
        accounts = pwd.getpwall()
        require(not any(account.pw_uid == 1000 for account in accounts),
                'The pre-account gate must run before UID1000 exists.')
        interactive = [account for account in accounts
                       if account.pw_uid >= 1000
                       and not (account.pw_uid == 65534 and account.pw_name == 'nobody')
                       and Path(account.pw_shell).name not in ('nologin', 'false')]
        require(not interactive, 'The pre-account gate found an existing interactive account.')
        ordinary = pwd.getpwnam('nobody')
        require(ordinary.pw_uid == 65534 and ordinary.pw_gid != 0,
                'The pre-account query requires the existing unprivileged nobody identity.')
    else:
        ordinary = pwd.getpwuid(1000)
        require(ordinary.pw_uid == 1000 and ordinary.pw_uid != 0,
                'Ordinary readability requires the existing UID1000 login account.')
    return ordinary


def ordinary_refs(ordinary, pre_account=False):
    query = (
        'import os,gi,json;'
        f'assert os.geteuid()=={ordinary.pw_uid} and os.geteuid()!=0;'
        'gi.require_version("Flatpak","1.0");from gi.repository import Flatpak;'
        'i=Flatpak.Installation.new_system(None);print(json.dumps({r.format_ref(): '
        '{"commit":r.get_commit(),"origin":r.get_origin()} for r in i.list_installed_refs(None)}))')
    if not pre_account:
        return json.loads(command('runuser', '-l', ordinary.pw_name, '-c',
                                  'python3 -c ' + shlex.quote(query)))
    # No login user exists yet. This owned disposable HOME avoids opening or
    # creating any system account profile; the query still uses the real
    # system Flatpak installation after dropping to the actual nobody UID.
    with tempfile.TemporaryDirectory(prefix='luma-gate-readability-', dir='/var/tmp') as home:
        os.chown(home, ordinary.pw_uid, ordinary.pw_gid)
        return json.loads(command(
            'runuser', '-u', ordinary.pw_name, '--', 'env',
            '-u', 'DBUS_SESSION_BUS_ADDRESS', '-u', 'XDG_RUNTIME_DIR',
            '-u', 'DISPLAY', '-u', 'WAYLAND_DISPLAY',
            'HOME=' + home, 'XDG_DATA_HOME=' + home + '/share',
            'XDG_CONFIG_HOME=' + home + '/config', 'XDG_CACHE_HOME=' + home + '/cache',
            'python3', '-c', query))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pre-account', action='store_true',
                        help='Verify system-ref readability before any interactive account exists.')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'The disposable system-installation gate requires root.')
    require(required(APP), 'The OS must assign Leaf its signed application role.')
    # Invoke only the image owner's read-only validator, not its handoff/main.
    owner = runpy.run_path('/usr/libexec/luma-app-baseline',
                          run_name='luma_gate_baseline_contract')
    manifest = owner['validate'](BASELINE, platform.machine())
    require(MARKER.is_file() and not MARKER.is_symlink(),
            'The actual first-boot baseline must have completed before removal.')
    marker_before = fingerprint(MARKER)
    completion = owner['document'](MARKER)
    require(completion.get('schema') == 'org.projectluma.app-baseline-completion/v1'
            and completion.get('baseline_manifest_sha256') == fingerprint(BASELINE / 'manifest.json')['sha256'],
            'The durable completion marker does not identify this baseline.')
    require(Path('/usr/share/luma/first-party-app-roles.json').read_bytes()
            == (BASELINE / 'roles.json').read_bytes(), 'The deployed OS role contract changed.')
    installation = Flatpak.Installation.new_system(None)
    validate_remote(installation.get_remote_by_name('luma', None), 'luma')

    def refs():
        installation.drop_caches(None)
        result = {}
        for ref in installation.list_installed_refs(None):
            key = ref.format_ref()
            require(key not in result, 'The system has ambiguous installed refs.')
            result[key] = ref
        return result

    def identities():
        return {key: {'commit': ref.get_commit(), 'origin': ref.get_origin()}
                for key, ref in refs().items()}

    def remotes():
        installation.drop_caches(None)
        return sorted((remote.get_name(), remote.get_url(), remote.get_gpg_verify())
                      for remote in installation.list_remotes(None))

    def native_identity():
        packages = sorted(command('rpm', '-qa', '--qf', '%{NEVRA}\n').splitlines())
        booted = [row['checksum'] for row in json.loads(command('rpm-ostree', 'status', '--json'))['deployments']
                  if row.get('booted')]
        require(len(booted) == 1, 'The actual booted OS deployment is ambiguous.')
        return {'booted': booted[0],
                'all_rpm_sha256': hashlib.sha256('\n'.join(packages).encode()).hexdigest()}

    before = identities()
    expected = {row['ref']: {'commit': row['commit'], 'origin': 'luma'}
                for row in manifest['bundles']}
    require(len(before) == 27 and before == expected, 'The fresh signed 27-ref baseline is incomplete or changed.')
    require(completion.get('installed_or_preserved') == {key: value['commit'] for key, value in before.items()},
            'The first-boot completion did not record the installed baseline.')
    for key, ref in refs().items():
        verify_deployed_commit(installation, ref, exact_ref=key)
    leaf = installed(installation, APP)
    leaf_key = leaf.format_ref()
    row = next(row for row in manifest['bundles'] if row['ref'] == leaf_key)
    bundle = BASELINE / row['file']
    actual = Flatpak.BundleRef.new(Gio.File.new_for_path(str(bundle)))
    require(actual.format_ref() == leaf_key and actual.get_commit() == row['commit'],
            'The supported restore bundle has a different app identity.')
    native_before = native_identity()
    remotes_before = remotes()
    untouched = {key: value for key, value in before.items() if key != leaf_key}

    def restore():
        if leaf_key in refs():
            return
        # Exact normal first-boot install callback, without rerunning handoff.
        repository = installation.get_path().get_child('repo').get_path()
        command('flatpak', 'build-import-bundle', '--no-update-summary',
                '--ref=luma:' + leaf_key, repository, str(bundle))
        verify_deployed_commit(installation, actual, commit=row['commit'], exact_ref=leaf_key)
        command('flatpak', 'install', '--system', '--noninteractive', '--no-pull',
                '--no-deps', '--no-related', 'luma', leaf_key)

    # This one owned diagnostic data file catches a transaction accidentally
    # using delete-data; existing app profiles are neither opened nor removed.
    data = Path.home() / '.var/app' / APP / 'data'
    created = []
    parent = data
    while not parent.exists():
        created.append(parent)
        parent = parent.parent
    data.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.luma-gate-preserve-', dir=data)
    diagnostic = Path(name)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(os.urandom(32))
    data_before = fingerprint(diagnostic)
    attempted = False
    try:
        tx = removal_transaction(installation, leaf)
        tx.set_disable_related(True)
        operations = []

        def ready(transaction):
            actual_ops = transaction.get_operations()
            valid = (len(actual_ops) == 1 and actual_ops[0].get_ref() == leaf_key
                     and actual_ops[0].get_operation_type() == Flatpak.TransactionOperationType.UNINSTALL)
            if valid:
                operations.append({'ref': leaf_key, 'operation': 'uninstall'})
            return valid

        tx.connect('ready', ready)
        attempted = True
        require(tx.run(None), 'The maintained Depot removal transaction did not complete.')
        require(len(operations) == 1 and identities() == untouched,
                'Removal did not remove exactly Leaf while retaining the other 26 refs.')
        try:
            installed(installation, APP)
        except ValueError:
            pass
        else:
            raise ValueError('The native owner still resolved an uninstalled signed application.')
        require(fingerprint(diagnostic) == data_before, 'Removal deleted or changed the owned application data.')
        require(fingerprint(MARKER) == marker_before and native_identity() == native_before,
                'Removal changed the baseline marker or the native OS engines.')
        restore()
        restored = installed(installation, APP)
        require(restored.format_ref() == leaf_key and restored.get_commit() == row['commit'],
                'Restore did not authenticate the exact original signed app head.')
        require(identities() == before and remotes() == remotes_before,
                'Restore changed another installed head or created a different origin.')
        require(fingerprint(MARKER) == marker_before and native_identity() == native_before
                and fingerprint(diagnostic) == data_before,
                'Restore changed app data, the completion marker, or native engines.')
        ordinary = readability_account(args.pre_account)
        readable = ordinary_refs(ordinary, args.pre_account)
        require(readable == before, 'The restored 27-ref baseline is not readable by an ordinary user.')
        print(json.dumps({'result': 'PASS', 'removed_ref': leaf_key, 'restored_head': row['commit'],
                          'operations': operations, 'other_26_heads_unchanged': True,
                          'same_origin_and_remotes': True, 'native_engines_and_boot_unchanged': True,
                          'durable_marker_unchanged': True, 'app_data_preserved': True,
                          'ordinary_27_ref_readability': True,
                          'readability_phase': 'pre-account' if args.pre_account else 'installed-user',
                          'readability_uid': ordinary.pw_uid}, sort_keys=True))
    finally:
        if attempted:
            restore()  # Preserve the disposable installation even on a later failed assertion.
        diagnostic.unlink(missing_ok=True)
        for directory in created:
            try:
                directory.rmdir()
            except OSError:
                break


if __name__ == '__main__':
    main()
