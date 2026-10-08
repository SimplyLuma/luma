#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the emulator's downstream Android 13 allocator from pinned local sources.

No downloads. SOURCE_DIR contains hardware/core/logging Git repositories; NDK is
28.2.13676358. LIBCUTILS is extracted from the pinned arm64_only system image.
Output is an artifact candidate, not release acceptance or a device claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

REVISIONS = {
    'hardware': '0eb202d7ebd7d2410eb2f62c908c0341964a4829',
    'core': 'c0500ffe2cc283d61f1e3db1b2c979bfbd9beb8c',
    'logging': '373d54e772f303c441faf2e584a2edf3601b4181',
}
LIBCUTILS = '092f98bdaf933588703bc6b285f8f5032d106f4790ca5d06ea886a59559dae40'

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources', type=Path, required=True)
    p.add_argument('--ndk', type=Path, required=True)
    p.add_argument('--libcutils', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if digest(a.libcutils) != LIBCUTILS:
        p.error('libcutils does not match the pinned Android system image')
    if 'Pkg.Revision = 28.2.13676358' not in (a.ndk / 'source.properties').read_text():
        p.error('NDK revision must be 28.2.13676358')
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    patch = Path(__file__).resolve().parents[2] / 'patches/android-hardware-libhardware/0001-luma-software-yuv-buffers.patch'
    for name, revision in REVISIONS.items():
        dst = out / name
        dst.mkdir()
        # git archive ignores dirty working-tree files and exports the exact commit.
        archive = subprocess.run(['git', '-C', str(a.sources / name), 'archive', revision], check=True, capture_output=True).stdout
        subprocess.run(['tar', '-xf', '-', '-C', str(dst)], input=archive, check=True)
    subprocess.run(['patch', '-p1', '-i', str(patch)], cwd=out / 'hardware', check=True)
    host = 'darwin-x86_64' if platform.system() == 'Darwin' else 'linux-x86_64'
    compiler = a.ndk / 'toolchains/llvm/prebuilt' / host / 'bin/aarch64-linux-android33-clang++'
    module = out / 'gralloc.default.so'
    cmd = [str(compiler), '-shared', '-fPIC', '-O2', '-std=gnu++17', '-static-libstdc++',
           '-fno-exceptions', '-fno-rtti', '-Wno-c99-designator', '-Wno-deprecated-declarations',
           '-DLOG_TAG="gralloc"', '-Wl,-z,defs', '-Wl,-soname,gralloc.default.so',
           '-ffile-prefix-map=' + str(out) + '=/luma/gralloc-source']
    for include in ('hardware/include', 'core/libcutils/include', 'core/libsystem/include', 'core/libutils/include', 'logging/liblog/include'):
        cmd += ['-I', str(out / include)]
    cmd += [str(out / 'hardware/modules/gralloc' / f) for f in ('gralloc.cpp', 'mapper.cpp', 'framebuffer.cpp')]
    cmd += [str(a.libcutils.resolve()), '-llog', '-o', str(module)]
    subprocess.run(cmd, check=True)
    # Build the runtime acceptance probe alongside the module; it is test-only
    # and deliberately excluded from the exported emulator bundle.
    probe = Path(__file__).resolve().parents[2] / 'tests/android/software-gralloc-probe.cpp'
    test_cmd = [str(compiler), '-std=c++17', '-static-libstdc++']
    for include in ('hardware/include', 'hardware/modules/gralloc', 'core/libcutils/include', 'core/libsystem/include', 'logging/liblog/include'):
        test_cmd += ['-I', str(out / include)]
    test_cmd += [str(probe), '-ldl', '-llog', '-o', str(out / 'software-gralloc-probe')]
    subprocess.run(test_cmd, check=True)
    licenses = out / 'licenses'
    licenses.mkdir()
    for name in REVISIONS:
        source = out / name / 'NOTICE'
        if source.is_file():
            shutil.copyfile(source, licenses / (name + '-NOTICE'))
    for name in ('NOTICE', 'NOTICE.toolchain'):
        shutil.copyfile(a.ndk / name, licenses / ('NDK-' + name))
    upstream = patch.parent / 'upstream'
    for source in upstream.glob('*.patch'):
        shutil.copyfile(source, licenses / source.name)
    (out / 'build-info.json').write_text(json.dumps({
        'ndk_version': '28.2.13676358', 'android_api': 33, 'architecture': 'aarch64',
        'source_revisions': REVISIONS, 'patch_sha256': digest(patch),
        'waydroid_vendor_revision': '1b95b85221f4faaa357932fa5e93eacb7430f636',
        'waydroid_patches': {f.name: digest(f) for f in sorted(upstream.glob('*.patch'))},
        'module_sha256': digest(module), 'libcutils_sha256': LIBCUTILS,
    }, indent=2) + '\n')
    print(digest(module), module.name)

if __name__ == '__main__':
    main()
