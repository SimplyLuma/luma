#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Replace a signed medium payload while retaining a qualified Atlas runtime.

Run inside the existing native Atlas tools image. This produces media, never a
qualification result. The caller supplies the authenticated donor ISO digest and
runtime source revision; normal boot/install qualification remains separate.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import tempfile
import time

RUNTIME_PATHS = (
    'src/luma-installer-atlas', 'config/install/atlas',
    'packaging/rpm/luma-installer-atlas.spec',
    'scripts/install/atlas-iso/templates',
    'scripts/install/atlas-iso/luma-atlas-runtime.tmpl',
)
UNCHANGED_FILES = ('/images/install.img', '/images/pxeboot/vmlinuz',
                   '/images/pxeboot/initrd.img')


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def runtime_source_proof(source, old, new):
    def git(*args):
        return subprocess.check_output(['git', '-c', f'safe.directory={source}',
                                        '-C', str(source), *args], text=True)
    paths = list(RUNTIME_PATHS)
    files = git('ls-tree', '-r', '--name-only', old, 'scripts/install/atlas-iso').splitlines()
    paths += [p for p in files if Path(p).name.startswith('prepare-runtime-')
              or Path(p).name == 'prepare-lorax-boot.py']
    changed = git('diff', '--name-only', old, new, '--', *paths).splitlines()
    if changed:
        raise ValueError(f'installer runtime source changed: {changed}')
    return paths


def verify_at_clock(repo, commit, key, epoch, directory):
    # Verify actual detached signature at the installer's minimum RTC. A newly
    # signed payload can require a newer clock even when its runtime is unchanged.
    import gi
    gi.require_version('OSTree', '1.0')
    from gi.repository import Gio, OSTree
    repository = OSTree.Repo.new(Gio.File.new_for_path(str(repo)))
    repository.open(None)
    _, metadata = repository.read_commit_detached_metadata(commit, None)
    signatures = metadata.unpack().get('ostree.gpgsigs', [])
    if not signatures:
        raise ValueError('payload commit has no detached GPG signatures')
    data = directory / 'commit'
    data.write_bytes((repo / 'objects' / commit[:2] / (commit[2:] + '.commit')).read_bytes())
    # GnuPG creates Unix sockets beneath its home. Release work paths can
    # exceed sockaddr_un even though all ordinary file accesses succeed.
    # This fresh, private public-key-only home has a bounded path length.
    with tempfile.TemporaryDirectory(prefix='luma-clock-gpg-', dir='/tmp') as temporary:
        home = Path(temporary)
        run('gpg', '--batch', '--homedir', home, '--no-autostart', '--import', key,
            stdout=subprocess.DEVNULL)
        for index, signature in enumerate(signatures):
            sig = directory / f'signature-{index}'
            sig.write_bytes(bytes(signature))
            check = subprocess.run(['gpg', '--batch', '--homedir', str(home),
                                    '--no-autostart', '--faked-system-time', str(epoch) + '!',
                                    '--verify', str(sig), str(data)], capture_output=True)
            if check.returncode == 0:
                return
    raise ValueError('new payload signature is invalid at retained runtime clock; '
                     'refresh the runtime clock deliberately before reusing it')


def inventory(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            relative = str(path.relative_to(root))
            value = [info.st_mode, info.st_uid, info.st_gid,
                     {key: os.getxattr(path, key, follow_symlinks=False).hex()
                      for key in os.listxattr(path, follow_symlinks=False)}]
            if path.is_symlink():
                value += [os.readlink(path)]
            elif path.is_file():
                value += [sha(path)]
            # Only the intentional epoch file's mtime is exempted below.
            value += [info.st_mtime_ns]
            result[relative] = value
    return result


def positive_integer(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return value


def preflight_tools(refresh):
    tools = ['gpg', 'ostree', 'xorriso', 'unsquashfs', 'mcopy', 'python3']
    if refresh:
        tools += ['mksquashfs', 'cpio']
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise ValueError(f'missing runtime reuse tools: {", ".join(missing)}')


def update_treeinfo(body, checksums):
    # Parse and reject ambiguity BEFORE extracting/recompressing the full
    # stage2. None means validation-only; actual digests are applied afterward.
    for destination, digest in checksums.items():
        pattern = r'(?m)^(' + re.escape(destination.lstrip('/')) + r'\s*=\s*)sha256:[0-9a-f]{64}$'
        if len(re.findall(pattern, body)) > 1:
            raise ValueError('ambiguous runtime image checksum in .treeinfo')
        if digest is not None:
            body = re.sub(pattern, lambda match: match[1] + 'sha256:' + digest, body)
    return body


def refresh_clock(old, work, epoch, processors=2, cache_mib=512):
    root = work / 'stage2-clock'
    run('unsquashfs', '-no-progress', '-processors', str(processors),
        '-d', root, old / 'install.img', stdout=subprocess.DEVNULL)
    before = inventory(root)
    clock = root / 'usr/lib/clock-epoch'
    env = root / 'usr/share/luma-installer-atlas/media.env'
    original = env.read_text()
    updated = '\n'.join(f'clock_epoch={epoch}' if line.startswith('clock_epoch=') else line
                        for line in original.splitlines()) + '\n'
    env.write_text(updated)
    os.utime(clock, (epoch, epoch))
    after = inventory(root)
    if set(before) != set(after):
        raise ValueError('clock refresh changed runtime path set')
    for path in before:
        left, right = before[path][:], after[path][:]
        if path == 'usr/lib/clock-epoch':
            left[-1] = right[-1]
        elif path == 'usr/share/luma-installer-atlas/media.env':
            left[-2:] = right[-2:]
        if left != right:
            raise ValueError(f'clock refresh changed unrelated runtime metadata/content: {path}')
    # Preserve the complete runtime tree, ACLs, labels and ownership. Only the
    # compression container is regenerated; no RPM transaction or Lorax runs.
    stage2 = work / 'install-clock.img'
    run('mksquashfs', root, stage2, '-noappend', '-comp', 'xz', '-b', '1048576',
        '-processors', str(processors), '-mem', f'{cache_mib}M', '-no-progress')
    # Linux unpacks concatenated initramfs archives in order. Keep every old
    # compressed byte and append one reproducible override for clock-epoch.
    overlay = work / 'clock-overlay'
    (overlay / 'usr/lib').mkdir(parents=True)
    target = overlay / 'usr/lib/clock-epoch'
    target.touch(mode=0o644)
    os.utime(target, (epoch, epoch))
    archive = subprocess.check_output(['cpio', '--null', '-o', '-H', 'newc', '--reproducible'],
                                      input=b'usr/lib/clock-epoch\0', cwd=overlay)
    initrd = work / 'initrd-clock.img'
    with initrd.open('xb') as destination, (old / 'initrd.img').open('rb') as origin:
        import shutil
        shutil.copyfileobj(origin, destination, 1024 * 1024)
        destination.write(gzip.compress(archive, mtime=0))
    (work / 'CLOCK-ONLY-DELTA.json').write_text(json.dumps(dict(
        original_initrd_sha256=sha(old / 'initrd.img'),
        appended_initramfs_sha256=hashlib.sha256(archive).hexdigest(),
        original_stage2_sha256=sha(old / 'install.img'),
        exact_stage2_tree_changes=['usr/lib/clock-epoch:mtime',
                                  'usr/share/luma-installer-atlas/media.env:clock_epoch'],
        clock_epoch=epoch), indent=2) + '\n')
    return {'/images/install.img': stage2, '/images/pxeboot/initrd.img': initrd}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('donor', 'donor-sha256', 'source', 'donor-revision',
                 'source-revision', 'payload-repo', 'ref', 'commit', 'key',
                 'output', 'work'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--clock-epoch', type=int, help='deliberate clock-only runtime refresh')
    parser.add_argument('--squashfs-processors', type=positive_integer, default=2,
                        help='compression workers within the caller memory budget (default: 2)')
    parser.add_argument('--squashfs-cache-mib', type=positive_integer, default=512,
                        help='mksquashfs cache MiB, excluding worker/OS memory (default: 512)')
    parser.add_argument('--arch', choices=('x86_64', 'aarch64'), required=True)
    args = parser.parse_args()
    preflight_tools(args.clock_epoch is not None)
    here = Path(__file__).resolve().parent
    source, donor, repo = map(lambda p: Path(p).resolve(),
                               (args.source, args.donor, args.payload_repo))
    output, work = Path(args.output).absolute(), Path(args.work).absolute()
    if output.exists() or work.exists() or donor == output:
        raise ValueError('output and work must be new paths')
    if len(args.commit) != 64 or any(c not in '0123456789abcdef' for c in args.commit):
        raise ValueError('expected exact OSTree commit checksum')
    paths = runtime_source_proof(source, args.donor_revision, args.source_revision)
    if sha(donor) != args.donor_sha256:
        raise ValueError('authenticated donor digest mismatch')
    from pylorax.cmdline import mkksiso
    volid, entries = mkksiso.GetISODetails(str(donor))
    if not volid or not all(p.lstrip('/') in entries for p in UNCHANGED_FILES):
        raise ValueError('donor lacks the qualified installer runtime')
    work.mkdir(mode=0o700)
    old = work / 'donor-runtime'
    old.mkdir()
    for item in UNCHANGED_FILES:
        run('xorriso', '-osirrox', 'on', '-indev', donor,
            '-extract', item, old / Path(item).name, stdout=subprocess.DEVNULL)
    runtime = work / 'stage2'
    run('unsquashfs', '-no-progress', '-d', runtime, old / 'install.img',
        'usr/share/luma-installer-atlas/media.env',
        'usr/share/luma-installer-atlas/luma-release.gpg', stdout=subprocess.DEVNULL)
    settings = dict(line.split('=', 1) for line in
                    (runtime / 'usr/share/luma-installer-atlas/media.env').read_text().splitlines()
                    if '=' in line and not line.startswith('#'))
    if settings.get('volid') != volid or settings.get('ref') != args.ref:
        raise ValueError('retained label/ref differs from embedded runtime binding')
    if settings.get('architecture') != args.arch or args.ref.split('/')[2] != args.arch:
        raise ValueError('payload architecture differs from runtime architecture')
    actual = subprocess.check_output(['ostree', f'--repo={repo}', 'rev-parse', args.ref], text=True).strip()
    if actual != args.commit:
        raise ValueError('payload ref does not resolve to exact candidate')
    if sha(runtime / 'usr/share/luma-installer-atlas/luma-release.gpg') != sha(args.key):
        raise ValueError('payload key differs from embedded installer release key')
    epoch = args.clock_epoch or int(settings['clock_epoch'])
    if epoch < int(settings['clock_epoch']) or epoch > int(time.time()) + 300:
        raise ValueError('clock refresh must advance within current build time')
    with tempfile.TemporaryDirectory(dir=work) as temporary:
        verify_at_clock(repo, args.commit, Path(args.key), epoch, Path(temporary))
    overrides = {}
    if epoch != int(settings['clock_epoch']):
        treeinfo = None
        if '.treeinfo' in entries:
            treeinfo = work / '.treeinfo'
            run('xorriso', '-osirrox', 'on', '-indev', donor,
                '-extract', '/.treeinfo', treeinfo, stdout=subprocess.DEVNULL)
            body = update_treeinfo(treeinfo.read_text(), dict.fromkeys(
                ('/images/install.img', '/images/pxeboot/initrd.img')))
        overrides = refresh_clock(old, work, epoch, args.squashfs_processors,
                                  args.squashfs_cache_mib)
        if treeinfo is not None:
            treeinfo.write_text(update_treeinfo(body, {destination: sha(path)
                                                      for destination, path in overrides.items()}))
            overrides['/.treeinfo'] = treeinfo

    payload = work / 'luma'
    payload.mkdir()
    # A read-only symlink avoids copying a signed object corpus. mkksiso maps
    # this directory into the NEW image; its replacement hook removes old repo.
    (payload / 'repo').symlink_to(repo, target_is_directory=True)
    # Extra image mappings are inserted by the source-owned wrapper immediately
    # before its normal output finalization (including implantisomd5).
    command = ['python3', here / 'run-mkksiso.py', '--replace-payload-repository']
    for destination, path in overrides.items():
        command += ['--runtime-clock-map', str(path), destination]
    run(*command, '-V', volid, '-a', payload, donor, output)
    if mkksiso.GetISODetails(str(output))[0] != volid:
        raise ValueError('remaster changed embedded stage2 label')
    new = work / 'new-runtime'
    new.mkdir()
    bodies = {}
    for item in UNCHANGED_FILES:
        target = new / Path(item).name
        run('xorriso', '-osirrox', 'on', '-indev', output, '-extract', item, target,
            stdout=subprocess.DEVNULL)
        bodies[item] = sha(target)
        if bodies[item] != sha(overrides.get(item, old / Path(item).name)):
            raise ValueError(f'remaster changed qualified runtime {item}')
    boot = work / 'boot-proof'
    boot.mkdir()
    for item, name in (('/boot/grub2', 'grub2'), ('/EFI/BOOT/grub.cfg', 'efi-grub.cfg')):
        run('xorriso', '-osirrox', 'on', '-indev', output, '-extract', item, boot / name,
            stdout=subprocess.DEVNULL)
    run('python3', here / 'prepare-lorax-boot.py', '--verify-iso', boot, args.arch)
    run('python3', source / 'scripts/boot/check-grub-fonts.py', boot / 'grub2/themes/luma')
    run('python3', here / 'extract-iso-efi.py', output, boot / 'efiboot.img')
    run('mcopy', '-i', boot / 'efiboot.img', '::/EFI/BOOT/grub.cfg', boot / 'embedded-grub.cfg')
    if (boot / 'efi-grub.cfg').read_bytes() != (boot / 'embedded-grub.cfg').read_bytes():
        raise ValueError('embedded UEFI menu differs from visible menu')
    proof = dict(result='BUILT_REUSED_RUNTIME_NOT_QUALIFIED', architecture=args.arch,
                 donor_sha256=args.donor_sha256, donor_revision=args.donor_revision,
                 source_revision=args.source_revision, unchanged_source_paths=paths,
                 retained_volume_id=volid, retained_clock_epoch=int(settings['clock_epoch']), clock_epoch=epoch,
                 clock_only_refresh=bool(overrides),
                 squashfs_processors=args.squashfs_processors if overrides else None,
                 squashfs_cache_mib=args.squashfs_cache_mib if overrides else None,
                 payload_commit=args.commit, payload_ref=args.ref, runtime_sha256=bodies,
                 payload_signature_valid_at_runtime_clock=True,
                 iso_sha256=sha(output), iso_size=output.stat().st_size)
    Path(str(output) + '.runtime-reuse.json').write_text(json.dumps(proof, indent=2) + '\n')
    Path(str(output) + '.sha256').write_text(f"{proof['iso_sha256']}  {output.name}\n")


if __name__ == '__main__':
    main()
