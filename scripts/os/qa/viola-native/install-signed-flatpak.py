#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Seed the fresh fixture with exact signed refs and retain production policy."""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import gi
gi.require_version('Flatpak', '1.0')
gi.require_version('OSTree', '1.0')
from gi.repository import Flatpak
from luma_installer.depot_flatpak import read_repo_file, validate_remote
from luma_installer.native_app_roles import verify_deployed_commit


def run(*argv):
    return subprocess.check_output(argv, text=True, timeout=240)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mirror', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    home = Path('/home/luma-gate-browser-opens')
    assert os.getuid() == 1100 and Path.home() == home
    assert not (home / '.local/share/flatpak').exists(), 'Fresh installation required'
    assert 'FLATPAK_USER_DIR' not in os.environ
    profile = json.loads((args.mirror / 'INPUTS.json').read_text())['profile']
    descriptor = read_repo_file(args.mirror / 'site/luma.flatpakrepo')
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_string(descriptor.decode())
    official_url = config['Flatpak Repo']['Url']
    config['Flatpak Repo']['Url'] = (args.mirror / 'repo').as_uri()
    local_descriptor = args.out / 'local-input.flatpakrepo'
    with local_descriptor.open('x') as stream:
        config.write(stream)
    run('flatpak', 'remote-add', '--user', '--from', 'luma', str(local_descriptor))
    installation = Flatpak.Installation.new_user(None)
    # This is a normal signed-summary lookup; the retained summary advertises
    # other cohort refs, whose objects are deliberately absent from the mirror.
    advertised = {ref.format_ref(): ref.get_commit() for ref in
                  installation.list_remote_refs_sync('luma', None)}
    assert all(advertised.get(ref) == commit for ref, commit in profile['refs'].items())
    run('flatpak', 'install', '--user', '--noninteractive', '--assumeyes',
        '--no-related', '--no-deps', 'luma', *sorted(profile['refs']))
    # Ordinary offline seeding ends with the maintained official remote URL.
    # Keep signature checking enabled while importing and after restoring it.
    run('flatpak', 'remote-modify', '--user', '--url=' + official_url, 'luma')
    installation = Flatpak.Installation.new_user(None)
    validate_remote(installation.get_remote_by_name('luma', None), 'luma')
    installed = {ref.format_ref(): ref for ref in installation.list_installed_refs(None)}
    assert set(installed) == set(profile['refs'])
    for name, commit in profile['refs'].items():
        ref = installed[name]
        assert ref.get_commit() == commit and ref.get_origin() == 'luma'
        verify_deployed_commit(installation, ref, exact_ref=name)
    app_ref = next(name for name in profile['refs'] if name.startswith('app/'))
    files = Path(installed[app_ref].get_deploy_dir()) / 'files'
    engine_sha = digest(files / 'lib/viola/browser/chrome')
    assert engine_sha == digest(Path('/opt/viola/browser/chrome'))
    native = Path('/usr/share/viola-browser/luma-host')
    native_sources = {str(path.relative_to(native)): digest(path) for path in native.rglob('*')
                      if path.is_file() and path.suffix != '.pyc' and '__pycache__' not in path.parts}
    app_host = files / 'share/viola-browser/luma-host'
    app_sources = {str(path.relative_to(app_host)): digest(path) for path in app_host.rglob('*')
                   if path.is_file() and path.suffix != '.pyc' and '__pycache__' not in path.parts}
    assert all(app_sources.get(name) == value for name, value in native_sources.items()), 'Signed Flatpak host differs from native38 source'
    assert (files / 'share/viola-browser/native-engine-profile.json').read_bytes() == Path('/usr/share/viola-browser/native-engine-profile.json').read_bytes()
    for mode in ('--user', '--system'):
        for identity in ((), ('com.rhyme.viola',)):
            assert not run('flatpak', 'override', mode, '--show', *identity).strip(), 'Fixture permission overrides are forbidden'
    (args.out / 'flatpak-permissions.txt').write_text(run('flatpak', 'info', '--user', '--show-permissions', app_ref))
    # Flatpak 1.18's flatpak-context.c maps these normal XDG roots beneath
    # the application's own data directory. The live observer also checks
    # the real process environment; no command or environment overrides run.
    app_data = home / '.var/app/com.rhyme.viola'
    state_root = str(app_data / '.local/state/viola-luma/sessions')
    profile_path = str(app_data / 'data/viola-luma/profile')
    document = {'scope': 'EXACT_SIGNED_FLATPAK_INSTALLATION_NOT_RUNTIME_ACCEPTANCE',
                'app_ref': app_ref, 'app_commit': profile['refs'][app_ref],
                'runtime_ref': 'runtime/org.projectluma.Platform/aarch64/44',
                'runtime_commit': profile['refs']['runtime/org.projectluma.Platform/aarch64/44'],
                'refs': profile['refs'], 'source': profile['source'], 'remote_url': official_url,
                'engine_sha256': engine_sha,
                'host_sources': app_sources, 'state_root': state_root, 'profile': profile_path}
    (args.out / 'flatpak-deployment.json').write_text(json.dumps(document, indent=2) + '\n')
    print(json.dumps(document, sort_keys=True))


if __name__ == '__main__':
    main()
