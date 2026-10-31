#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the real image host-selection Make fragment and its default control."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def fragment(source):
    start = source.index('# BUILD_OS is the real host doing the build.')
    end = source.index('\nifeq ($(HOST_OS),)', start)
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('android_make', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    original = (args.android_make / 'core/envsetup.mk').read_text()
    with tempfile.TemporaryDirectory(prefix='luma-image-host-tools-') as directory:
        work = Path(directory)
        (work / 'core').mkdir()
        source = work / 'core/envsetup.mk'
        source.write_text(original)
        subprocess.run(['git', 'apply', '--unsafe-paths', '--directory=' + str(work),
                        str(root / 'patches/android-make/0001-luma-native-image-host-tools.patch')],
                       cwd=work, check=True)
        def run(text, host, cross, flag):
            unit = work / 'policy.mk'
            unit.write_text('TARGET_ARCH := x86_64\nTARGET_2ND_ARCH := x86\n' + fragment(text) + '''
.PHONY: probe
probe:
	@printf '%s|%s|%s|%s|%s|%s\\n' '$(BUILD_OS)' '$(HOST_CROSS_OS)' '$(HOST_CROSS_ARCH)' '$(HOST_CROSS_2ND_ARCH)' '$(TARGET_ARCH)' '$(TARGET_2ND_ARCH)'
''')
            environment = dict(PATH=os.defpath, LANG='C.UTF-8', HOST_OS=host,
                HOST_CROSS_OS=cross, HOST_CROSS_ARCH='x86', HOST_CROSS_2ND_ARCH='x86_64',
                BUILD_HOST_static='', LUMA_ANDROID_IMAGE_HOST_ONLY=flag)
            return subprocess.check_output(['make', '--no-print-directory', '-rR', '-f', str(unit), 'probe'],
                cwd=work, env=environment, text=True, timeout=10).strip().split('|')
        patched = source.read_text()
        cases = (('linux', ''), ('linux', 'windows'), ('linux', 'linux_bionic'), ('darwin', ''))
        for host, cross in cases:
            assert run(patched, host, cross, '') == run(original, host, cross, '')
            selected = run(patched, host, cross, '1')
            if selected != [host, '', '', '', 'x86_64', 'x86']:
                raise SystemExit(f'Image host policy failed: {selected}')
        control = run(original, 'linux', '', '1')
        assert control[1] == 'windows', control
        print('PASS: four real Make host cases preserve defaults/guest ABI fields; image-only flag removes host cross variants; upstream control differs')


if __name__ == '__main__':
    main()
