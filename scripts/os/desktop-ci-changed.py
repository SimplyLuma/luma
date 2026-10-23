#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Avoid rebuilding qualified native providers after unrelated PR pushes."""
import argparse
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
INPUTS = {
    'webkit': ('scripts/packages/build-webkitgtk-luma.sh',
               'scripts/packages/run-in-rpm-builder.sh', 'config/desktop/inputs.env',
               'config/shared/webkitgtk-inputs.env', 'packaging/rpm/webkitgtk-luma.spec',
               'patches/webkitgtk/'),
    'browser': ('scripts/os/build-desktop-browser.sh', 'scripts/packages/build-viola-browser.sh',
                'scripts/packages/run-in-rpm-builder.sh', 'config/desktop/inputs.env',
                'packaging/rpm/viola-browser-stable.spec', 'src/external/viola/chromium-linux/'),
}


def affected(component, paths):
    if component not in INPUTS:
        raise ValueError('Unknown desktop provider')
    # Workflow/selector-only metadata changes do not invalidate a qualified
    # package. workflow_dispatch explicitly requests a fresh build when needed.
    return any(path.startswith(prefix) for path in paths for prefix in INPUTS[component])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', required=True, choices=INPUTS)
    parser.add_argument('--base', default='')
    args = parser.parse_args()
    if not args.base or set(args.base) == {'0'}:
        print('true')
        return
    if not re.fullmatch(r'[a-f0-9]{40,64}', args.base):
        raise ValueError('Expected an exact previous commit identity')
    data = subprocess.check_output(['git', '-C', str(ROOT), 'diff', '--name-only', '-z',
                                    args.base, 'HEAD', '--'], text=True)
    print('true' if affected(args.component, filter(None, data.split('\0'))) else 'false')


if __name__ == '__main__':
    main()
