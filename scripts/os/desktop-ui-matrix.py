#!/usr/bin/env python3
"""Select native UI rebuilds from the source delta, preserving both CPUs."""
import argparse
import json
import subprocess

COMPONENTS = ['gnome-control-center', 'nautilus', 'gnome-shell']
SHARED_FILES = {
    'config/desktop/inputs.env', 'scripts/os/build-desktop-ui.sh',
    'scripts/os/admit-desktop-toolkit.py', 'scripts/packages/run-in-rpm-builder.sh',
    'scripts/packages/spec-release.sh', 'scripts/packages/patch-series.sh',
    'scripts/packages/fetch-source-rpm.sh', 'packaging/rpm/luma-developer-platform.spec',
    'packaging/rpm/google-figtree-fonts.spec', 'LICENSE.md',
}
SHARED_PREFIXES = (
    'patches/gtk4/', 'patches/libadwaita/', 'src/luma-platform/',
    'scripts/fonts/', 'packaging/fontconfig/', 'assets/icon-theme/Prairie/',
    'tests/toolkit-native/',
)
PROVIDER_RECIPES = {
    f'scripts/packages/build-{name}.sh'
    for name in ('gtk4', 'libadwaita', 'luma-developer-platform', 'figtree-fonts')
}

def select(paths):
    if any(p in SHARED_FILES or p in PROVIDER_RECIPES or p.startswith(SHARED_PREFIXES) for p in paths):
        return COMPONENTS.copy()
    return [component for component in COMPONENTS if any(
        p in {f'scripts/packages/build-{component}.sh', f'scripts/packages/check-{component}-rpm.sh'}
        or p.startswith((f'patches/{component}/', f'tests/{component}/'))
        for p in paths)]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    args = parser.parse_args()
    components = COMPONENTS.copy()
    if args.base:
        try:
            diff = subprocess.check_output(['git', 'diff', '--name-only', '-z', args.base, 'HEAD', '--'], stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            pass  # Missing base: rebuild everything rather than silently omit checks.
        else:
            components = select(diff.decode().split('\0'))
    print(json.dumps(components))

if __name__ == '__main__':
    main()
