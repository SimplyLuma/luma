#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Retain Lorax's EFI partition update and refresh its ISO-file boot route."""
from pathlib import Path
import shutil
import sys


def replace_payload_command(command, clock_maps=()):
    """Remove only the old medium repository before the normal new /luma map."""
    if command and command[0] == 'xorriso' and '-outdev' in command:
        maps = [i for i, item in enumerate(command) if item == '-map']
        payload = [i for i in maps if command[i + 2].strip('/') == 'luma']
        if len(payload) != 1 or '-indev' not in command:
            raise ValueError('expected exactly one normal mkksiso /luma mapping')
        index = payload[0]
        repository = (Path(command[index + 1]) / 'repo').resolve(strict=True)
        if not repository.is_dir():
            raise ValueError('replacement payload must be a repository directory')
        # Map the real directory, not a host-only symlink into the new image.
        return (command[:index] + [arg for path, destination in clock_maps
                                  for arg in ('-map', str(path), destination)]
                + ['-rm_r', '/luma/repo', '--',
                '-map', str(repository), '/luma/repo'] + command[index + 3:])
    return command


def check_target_discinfo(path, architecture):
    """Bind a data-only remaster to its explicit target, never the tools CPU."""
    if architecture not in ('x86_64', 'aarch64'):
        raise ValueError('unsupported data-only target architecture')
    path = Path(path)
    if not path.is_file():
        raise ValueError('target-bound media require .discinfo')
    lines = [line.strip() for line in path.read_text().splitlines()]
    if len(lines) < 3 or lines[2] != architecture:
        raise ValueError('donor .discinfo differs from the target architecture')


def main():
    from pylorax.cmdline import mkksiso
    target = None
    if '--data-only-architecture' in sys.argv:
        index = sys.argv.index('--data-only-architecture')
        if sys.argv.count('--data-only-architecture') != 1 or index + 1 >= len(sys.argv):
            raise ValueError('expected one explicit data-only target architecture')
        target = sys.argv[index + 1]
        if target not in ('x86_64', 'aarch64'):
            raise ValueError('unsupported data-only target architecture')
        del sys.argv[index:index + 2]
        # Lorax's default compares .discinfo to the host CPU. No target
        # executable runs here: retain EFI rebuilding and every other check,
        # replacing only that comparison with a mandatory target binding.
        mkksiso.CheckDiscinfo = lambda path: check_target_discinfo(path, target)
    clock_maps = []
    while '--runtime-clock-map' in sys.argv:
        index = sys.argv.index('--runtime-clock-map')
        path, destination = sys.argv[index + 1:index + 3]
        if destination not in ('/images/install.img', '/images/pxeboot/initrd.img', '/.treeinfo'):
            raise ValueError('clock refresh may map only the stage2, initrd and their checksums')
        clock_maps.append((Path(path).resolve(strict=True), destination))
        del sys.argv[index:index + 3]
    if clock_maps and '--replace-payload-repository' not in sys.argv:
        raise ValueError('clock mappings require an explicit payload replacement')
    if '--replace-payload-repository' in sys.argv:
        sys.argv.remove('--replace-payload-repository')
        original_run = mkksiso.subprocess.run

        def run(command, *args, **kwargs):
            return original_run(replace_payload_command(command, clock_maps), *args, **kwargs)

        mkksiso.subprocess.run = run
    original = mkksiso.RebuildEFIBoot

    def rebuild(input_iso, temporary):
        image = original(input_iso, temporary)
        # Lorax44 updates appended partition2 but leaves an existing
        # /images/efiboot.img unchanged. ARM El Torito still boots that file.
        # Include the same verified FAT bytes in its normal update list.
        if 'images/efiboot.img' in mkksiso.GetISODetails(input_iso)[1]:
            target = Path(temporary) / 'images/efiboot.img'
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image.name, target)
        return image

    mkksiso.RebuildEFIBoot = rebuild
    return mkksiso.main()


if __name__ == '__main__':
    sys.exit(main())
