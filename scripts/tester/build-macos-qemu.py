#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the Try Luma macOS QEMU system emulators: upstream release + VNC display-rate patch.

QEMU's VNC server refreshes every 30 ms and backs off to 3 s when idle, which caps
the embedded viewer near 32 fps and delays guest-initiated updates. The patch polls
at 8 ms while the screen changes and idles at 32 ms. Libraries come from Homebrew,
the same as the stock emulators the app bundle replaces.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, urllib.request
from pathlib import Path

VERSION = '11.1.0'
URL = f'https://download.qemu.org/qemu-{VERSION}.tar.xz'
SHA256 = '6ee1d1a61f68212476b27108c26da5f449dc09b626d42f8279ba0dc2e08fa858'
PATCHES = ['qemu-vnc-display-rate.patch']


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('work', type=Path)
    p.add_argument('--python', required=True, help='Python with setuptools for QEMU mkvenv')
    p.add_argument('--tarball', type=Path, help='Already downloaded qemu tarball')
    a = p.parse_args()
    if sys.platform != 'darwin' or os.uname().machine != 'arm64':
        p.error('Build on an Apple Silicon Mac.')
    brew = Path(os.environ.get('LUMA_PORT_BREW', '/opt/homebrew'))
    work = a.work.resolve(); work.mkdir(parents=True, exist_ok=True)
    (work / '.metadata_never_index').touch()
    tarball = a.tarball or work / f'qemu-{VERSION}.tar.xz'
    if not tarball.exists():
        urllib.request.urlretrieve(URL, tarball)
    if hashlib.file_digest(tarball.open('rb'), 'sha256').hexdigest() != SHA256:
        p.error('QEMU source tarball digest mismatch.')
    source = work / f'qemu-{VERSION}'
    shutil.rmtree(source, ignore_errors=True)
    # roms/ (unused firmware sources) carries an absolute symlink; skip it.
    subprocess.run(['tar', '-xJf', str(tarball), '-C', str(work), '--exclude', f'qemu-{VERSION}/roms'], check=True)
    patches = Path(__file__).resolve().parent / 'patches'
    for name in PATCHES:
        subprocess.run(['patch', '-p1', '-i', str(patches / name)], cwd=source, check=True)
    build = source / 'build'; build.mkdir()
    env = dict(os.environ, PATH=f"{brew / 'bin'}:{os.environ['PATH']}")
    subprocess.run(['../configure', '--python=' + a.python, '--target-list=aarch64-softmmu,x86_64-softmmu',
                    '--disable-bsd-user', '--disable-download', '--disable-guest-agent', '--disable-docs',
                    '--enable-slirp', '--enable-zstd', '--enable-fdt=system', '--disable-sdl', '--disable-gtk',
                    '--enable-cocoa', '--enable-hvf', '--disable-werror', '-Dpvg=disabled',
                    f"--extra-cflags=-I{brew / 'include'}", f"--extra-ldflags=-L{brew / 'lib'}"],
                   cwd=build, env=env, check=True)
    subprocess.run(['nice', '-n', '10', 'ninja', '-j8', 'qemu-system-aarch64', 'qemu-system-x86_64'],
                   cwd=build, env=env, check=True)
    out = work / 'out'; shutil.rmtree(out, ignore_errors=True); out.mkdir()
    receipt = dict(version=VERSION, url=URL, sha256=SHA256, patches=PATCHES, files={})
    for name in ('qemu-system-aarch64', 'qemu-system-x86_64'):
        shutil.copy2(build / name, out / name)
        receipt['files'][name] = hashlib.file_digest((out / name).open('rb'), 'sha256').hexdigest()
    shutil.copy2(source / 'COPYING', out / 'COPYING')
    (out / 'QEMU-BUILD.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(out)


if __name__ == '__main__':
    main()
