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
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import struct
import stat
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


CLOCK_CHECKPOINT = 'clock-refresh.checkpoint.json'
CLOCK_FILES = ('install-clock.img', 'initrd-clock.img', 'CLOCK-ONLY-DELTA.json',
               'donor.discinfo', 'donor-runtime/install.img',
               'donor-runtime/vmlinuz', 'donor-runtime/initrd.img',
               'stage2/usr/share/luma-installer-atlas/media.env',
               'stage2/usr/share/luma-installer-atlas/luma-release.gpg')


def checksum(value):
    if not re.fullmatch('[0-9a-f]{64}', value):
        raise argparse.ArgumentTypeError('expected an exact SHA-256 checksum')
    return value


def checkpoint_file(work, name):
    path = work / name
    if path.is_symlink() or not path.resolve().is_relative_to(work.resolve()):
        raise ValueError('checkpoint file escapes the private work directory')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid():
        raise ValueError('checkpoint file must be regular and owned by this builder')
    digest = sha(path)
    after = path.stat()
    stable = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode')
    if any(getattr(before, field) != getattr(after, field) for field in stable):
        raise ValueError('checkpoint file changed while hashing')
    return dict(sha256=digest, size=after.st_size, mode=stat.S_IMODE(after.st_mode))


def checkpoint_names(inputs):
    return (*CLOCK_FILES, *(('donor.treeinfo',) if inputs['has_treeinfo'] else ()))


def save_clock_checkpoint(work, inputs):
    if work.is_symlink() or work.stat().st_uid != os.geteuid() or work.stat().st_mode & 0o077:
        raise ValueError('checkpoint requires a private builder-owned work directory')
    record = dict(schema=1, result='CLOCK_REFRESH_COMPLETE_NOT_QUALIFIED', inputs=inputs,
                  files={name: checkpoint_file(work, name) for name in checkpoint_names(inputs)})
    body = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode()
    descriptor, temporary = tempfile.mkstemp(prefix='.clock-checkpoint-', dir=work)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fchmod(stream.fileno(), 0o400)
            os.fsync(stream.fileno())
        # Exclusive atomic creation preserves an existing checkpoint on a retry.
        os.link(temporary, work / CLOCK_CHECKPOINT)
        directory = os.open(work, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)
    digest = hashlib.sha256(body).hexdigest()
    print('CLOCK_CHECKPOINT_SHA256=' + digest, flush=True)
    return digest


def load_clock_checkpoint(work, expected_sha256, inputs=None, verify_files=True):
    checksum(expected_sha256)
    if work.is_symlink() or work.stat().st_uid != os.geteuid() or work.stat().st_mode & 0o077:
        raise ValueError('resume requires a private builder-owned work directory')
    path = work / CLOCK_CHECKPOINT
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o400):
        raise ValueError('resume requires the immutable builder-owned checkpoint')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ValueError('checkpoint differs from the externally pinned SHA-256')
    record = json.loads(body)
    if record.get('schema') != 1 or record.get('result') != 'CLOCK_REFRESH_COMPLETE_NOT_QUALIFIED':
        raise ValueError('checkpoint does not attest a completed clock refresh')
    if inputs is not None and record['inputs'] != inputs:
        raise ValueError('resume semantic inputs or tools changed')
    if set(record['files']) != set(checkpoint_names(record['inputs'])):
        raise ValueError('checkpoint retained file set differs')
    if verify_files:
        for name, expected in record['files'].items():
            if checkpoint_file(work, name) != expected:
                raise ValueError('retained clock stage differs: ' + name)
    return record


def checkpoint_tools(args, here, mkksiso):
    # The operator pins the immutable image externally. Also record the actual
    # executable/module/script bytes consumed inside it, not just version text.
    tools = {}
    for name in ('python3', 'gpg', 'ostree', 'xorriso', 'unsquashfs', 'mksquashfs', 'cpio', 'mcopy'):
        executable = shutil.which(name)
        if executable is None:
            raise ValueError('missing checkpoint tool: ' + name)
        tools[name] = sha(Path(executable).resolve())
    scripts = {name: sha(here / name) for name in
               ('run-mkksiso.py', 'prepare-lorax-boot.py', 'extract-iso-efi.py')}
    scripts['reuse-installer-runtime.py'] = sha(Path(__file__))
    scripts['check-grub-fonts.py'] = sha(Path(args.source) / 'scripts/boot/check-grub-fonts.py')
    return dict(tools_image_id=args.tools_image_id, executable_sha256=tools,
                script_sha256=scripts, lorax_module_sha256=sha(Path(mkksiso.__file__)))


def checkpoint_inputs(args, repo, key, epoch, has_treeinfo, tools):
    metadata = {}
    for name in ('config', 'summary', 'summary.sig',
                 'objects/' + args.commit[:2] + '/' + args.commit[2:] + '.commitmeta'):
        path = repo / name
        metadata[name] = sha(path) if path.is_file() else None
    return dict(source_revision=args.source_revision, donor_revision=args.donor_revision,
                donor_sha256=args.donor_sha256, candidate=args.commit, ref=args.ref,
                architecture=args.arch, key_sha256=sha(key), clock_epoch=epoch,
                squashfs_processors=args.squashfs_processors, squashfs_cache_mib=args.squashfs_cache_mib,
                payload_metadata_sha256=metadata, has_treeinfo=has_treeinfo, **tools)


def clock_phase(old, work, epoch, inputs, processors, cache_mib, resume=None):
    if resume:
        load_clock_checkpoint(work, resume, inputs)
        return {'/images/install.img': work / 'install-clock.img',
                '/images/pxeboot/initrd.img': work / 'initrd-clock.img'}, resume
    overrides = refresh_clock(old, work, epoch, processors, cache_mib)
    digest = save_clock_checkpoint(work, inputs) if inputs is not None else None
    return overrides, digest


def pe_machine(path, architecture):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('UEFI executable lacks a DOS header')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if offset + 6 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError('UEFI executable lacks a PE header')
    expected = {'x86_64': 0x8664, 'aarch64': 0xaa64}[architecture]
    machine = struct.unpack_from('<H', data, offset + 4)[0]
    if machine != expected:
        raise ValueError('UEFI executable differs from the target architecture')
    return machine


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
    parser.add_argument('--tools-image-id', type=checksum,
                        help='externally pinned immutable tools image; enables sealed clock checkpoints')
    parser.add_argument('--resume', type=checksum, metavar='CHECKPOINT_SHA256',
                        help='reuse a completed clock stage only with this externally pinned digest')
    parser.add_argument('--arch', choices=('x86_64', 'aarch64'), required=True)
    args = parser.parse_args()
    if args.tools_image_id and any(not re.fullmatch('[0-9a-f]{40}', value)
                                   for value in (args.source_revision, args.donor_revision)):
        raise ValueError('checkpoints require exact source and donor Git revisions')
    preflight_tools(args.clock_epoch is not None)
    here = Path(__file__).resolve().parent
    source, donor, repo = map(lambda p: Path(p).resolve(),
                               (args.source, args.donor, args.payload_repo))
    output, work = Path(args.output).absolute(), Path(args.work).absolute()
    if output.exists() or donor == output or work.is_symlink():
        raise ValueError('output must be new and work may not be a symlink')
    if args.resume and (not args.tools_image_id or not work.is_dir()):
        raise ValueError('resume requires the original work directory and immutable tools-image pin')
    if not args.resume and work.exists():
        raise ValueError('initial work directory must be new')
    if len(args.commit) != 64 or any(c not in '0123456789abcdef' for c in args.commit):
        raise ValueError('expected exact OSTree commit checksum')
    from pylorax.cmdline import mkksiso
    # Pin the actual helper/wrapper/tool bytes before donor I/O or compression.
    toolchain = checkpoint_tools(args, here, mkksiso) if args.tools_image_id else None
    paths = runtime_source_proof(source, args.donor_revision, args.source_revision)
    if sha(donor) != args.donor_sha256:
        raise ValueError('authenticated donor digest mismatch')
    volid, entries = mkksiso.GetISODetails(str(donor))
    if not volid or not all(p.lstrip('/') in entries for p in UNCHANGED_FILES):
        raise ValueError('donor lacks the qualified installer runtime')
    retained = load_clock_checkpoint(work, args.resume, verify_files=False) if args.resume else None
    if retained is None:
        work.mkdir(mode=0o700)
    # Fail before extracting/recompressing the full runtime when an input's
    # metadata disagrees with the already selected target architecture.
    spec = importlib.util.spec_from_file_location('runtime_mkksiso', here / 'run-mkksiso.py')
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    discinfo = work / 'donor.discinfo'
    if retained is None:
        run('xorriso', '-osirrox', 'on', '-indev', donor,
            '-extract', '/.discinfo', discinfo, stdout=subprocess.DEVNULL)
    wrapper.check_target_discinfo(discinfo, args.arch)
    old = work / 'donor-runtime'
    runtime = work / 'stage2'
    if retained is None:
        old.mkdir()
        for item in UNCHANGED_FILES:
            run('xorriso', '-osirrox', 'on', '-indev', donor,
                '-extract', item, old / Path(item).name, stdout=subprocess.DEVNULL)
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
    epoch = (args.clock_epoch or (retained['inputs']['clock_epoch'] if retained
                                  else int(settings['clock_epoch'])))
    if epoch < int(settings['clock_epoch']) or epoch > int(time.time()) + 300:
        raise ValueError('clock refresh must advance within current build time')
    with tempfile.TemporaryDirectory(dir=work) as temporary:
        verify_at_clock(repo, args.commit, Path(args.key), epoch, Path(temporary))
    inputs = (checkpoint_inputs(args, repo, Path(args.key), epoch,
                                '.treeinfo' in entries, toolchain) if args.tools_image_id else None)
    if retained is not None and retained['inputs'] != inputs:
        raise ValueError('resume semantic inputs or tools changed')
    overrides = {}
    checkpoint_sha256 = None
    treeinfo_body = None
    if epoch != int(settings['clock_epoch']):
        if '.treeinfo' in entries:
            treeinfo = work / 'donor.treeinfo'
            if retained is None:
                run('xorriso', '-osirrox', 'on', '-indev', donor,
                    '-extract', '/.treeinfo', treeinfo, stdout=subprocess.DEVNULL)
            treeinfo_body = update_treeinfo(treeinfo.read_text(), dict.fromkeys(
                ('/images/install.img', '/images/pxeboot/initrd.img')))
        overrides, checkpoint_sha256 = clock_phase(old, work, epoch, inputs,
            args.squashfs_processors, args.squashfs_cache_mib, args.resume)
    elif retained is not None:
        raise ValueError('resume checkpoint must describe a completed clock refresh')
    attempt = Path(tempfile.mkdtemp(prefix='assembly-', dir=work))
    if treeinfo_body is not None:
        treeinfo = attempt / '.treeinfo'
        treeinfo.write_text(update_treeinfo(treeinfo_body, {destination: sha(path)
                                                          for destination, path in overrides.items()}))
        overrides['/.treeinfo'] = treeinfo

    payload = attempt / 'luma'
    payload.mkdir()
    # A read-only symlink avoids copying a signed object corpus. mkksiso maps
    # this directory into the NEW image; its replacement hook removes old repo.
    (payload / 'repo').symlink_to(repo, target_is_directory=True)
    # Extra image mappings are inserted by the source-owned wrapper immediately
    # before its normal output finalization (including implantisomd5).
    command = ['python3', here / 'run-mkksiso.py', '--data-only-architecture', args.arch,
               '--replace-payload-repository']
    for destination, path in overrides.items():
        command += ['--runtime-clock-map', str(path), destination]
    run(*command, '-V', volid, '-a', payload, donor, output)
    if mkksiso.GetISODetails(str(output))[0] != volid:
        raise ValueError('remaster changed embedded stage2 label')
    new = attempt / 'new-runtime'
    new.mkdir()
    bodies = {}
    for item in UNCHANGED_FILES:
        target = new / Path(item).name
        run('xorriso', '-osirrox', 'on', '-indev', output, '-extract', item, target,
            stdout=subprocess.DEVNULL)
        bodies[item] = sha(target)
        if bodies[item] != sha(overrides.get(item, old / Path(item).name)):
            raise ValueError(f'remaster changed qualified runtime {item}')
    boot = attempt / 'boot-proof'
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
    # Cross-host tools manipulate bytes only. Inspect actual rebuilt FAT PE
    # headers so a mislabeled donor cannot supply another CPU's boot loaders.
    efi_names = (('BOOTX64.EFI', 'grubx64.efi') if args.arch == 'x86_64'
                 else ('BOOTAA64.EFI', 'grubaa64.efi'))
    efi_machines = {}
    for name in efi_names:
        binary = boot / name
        run('mcopy', '-i', boot / 'efiboot.img', '::/EFI/BOOT/' + name, binary)
        efi_machines[name] = dict(machine=pe_machine(binary, args.arch), sha256=sha(binary))
    proof = dict(result='BUILT_REUSED_RUNTIME_NOT_QUALIFIED', architecture=args.arch,
                 donor_sha256=args.donor_sha256, donor_revision=args.donor_revision,
                 source_revision=args.source_revision, unchanged_source_paths=paths,
                 retained_volume_id=volid, retained_clock_epoch=int(settings['clock_epoch']), clock_epoch=epoch,
                 clock_only_refresh=bool(overrides), clock_refresh_resumed=bool(args.resume),
                 clock_checkpoint_sha256=checkpoint_sha256,
                 squashfs_processors=args.squashfs_processors if overrides else None,
                 squashfs_cache_mib=args.squashfs_cache_mib if overrides else None,
                 payload_commit=args.commit, payload_ref=args.ref, runtime_sha256=bodies,
                 payload_signature_valid_at_runtime_clock=True, efi_machines=efi_machines,
                 iso_sha256=sha(output), iso_size=output.stat().st_size)
    Path(str(output) + '.runtime-reuse.json').write_text(json.dumps(proof, indent=2) + '\n')
    Path(str(output) + '.sha256').write_text(f"{proof['iso_sha256']}  {output.name}\n")


if __name__ == '__main__':
    main()
