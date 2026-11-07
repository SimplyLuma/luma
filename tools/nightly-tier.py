#!/usr/bin/python3 -I
# SPDX-License-Identifier: Apache-2.0
"""Choose how much a nightly is tested from what changed since the last one.

  nightly-tier.py --from REV --to REV --previous-manifest FILE --manifest FILE

light   build, update yesterday's install, boot to a session, smoke the change
medium  light, plus sign-in and unlock, the keyboard and each core app once
full    fresh install from the ISO, update and rollback, on both CPUs

Every changed pinned package (luma-packages.manifest) and every changed image
input that ships without a package (config/, scripts/os/, the installer) is
classified; the highest tier wins, and an unrecognised package counts as
medium. Prints the tier, then the reasons.
"""
import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

TIERS = ('light', 'medium', 'full')

# Boot, update and install machinery, and the system the image is built from.
FULL_PACKAGES = ('kernel*', 'grub2*', 'shim*', 'ostree*', 'rpm-ostree*', 'bootc*', 'dracut*',
                 'systemd*', 'selinux-policy*', 'luma-installer*', 'luma-os-*')
MEDIUM_PACKAGES = ('gnome-shell*', 'mutter*', 'gtk3*', 'gtk4*', 'libadwaita*', 'libhandy*',
                   'gdm*', 'luma-keyring*', 'luma-android-runtime*', 'luma-shell-state*',
                   'gnome-settings-daemon*', 'gnome-control-center*', 'ibus*', 'mesa*',
                   'luma-display-config*', 'luma-developer-platform*', 'luma-ari*',
                   'luma-android-images*', 'fwupd*')
LIGHT_PACKAGES = ('prairie-*', 'luma-tide*', 'luma-terminal*', 'luma-monitor*', 'luma-search*',
                  'luma-home-widgets*', 'luma-agenda*', 'viola-*', 'nautilus*', 'gnome-calculator*',
                  'gnome-calendar*', 'gnome-text-editor*', 'google-*-fonts*', '*-fonts',
                  'luma-application-installer*', 'gnome-shell-extension-*')

# Only paths that reach the image without a package release. Packaged
# source (src/, patches/, assets/, packaging/) is judged by its package.
FULL_PATHS = ('config/os/*', 'scripts/os/*', 'src/luma-installer/*')
MEDIUM_PATHS = ('config/desktop/*', 'config/shared/*', 'config/depot/*')
PIN_PATHS = ('config/desktop/packages.txt', 'config/desktop/inputs.env')


def pins(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            nevra = line.split()[0]
            name = nevra.rsplit('.', 1)[0].rsplit('-', 2)[0]
            result[name] = nevra
    return result


def matches(name, patterns):
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def classify_package(name):
    if matches(name, FULL_PACKAGES):
        return 'full'
    if matches(name, LIGHT_PACKAGES) and not matches(name, MEDIUM_PACKAGES):
        return 'light'
    return 'medium'


def classify_path(path):
    if matches(path, PIN_PATHS):
        return None
    if matches(path, FULL_PATHS):
        return 'full'
    if matches(path, MEDIUM_PATHS):
        return 'medium'
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--from', dest='from_rev', required=True)
    parser.add_argument('--to', dest='to_rev', required=True)
    parser.add_argument('--previous-manifest', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    reasons = []
    before, after = pins(args.previous_manifest), pins(args.manifest)
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            reasons.append((classify_package(name), f'{name}: {before.get(name) or "new"} -> {after.get(name) or "removed"}'))
    changed = subprocess.run(['git', '-C', str(args.root), 'diff', '--name-only', args.from_rev, args.to_rev],
                             check=True, capture_output=True, text=True).stdout.split()
    for path in changed:
        tier = classify_path(path)
        if tier:
            reasons.append((tier, path))
    tier = max((TIERS.index(t) for t, _ in reasons), default=0)
    print(TIERS[tier])
    for level in reversed(TIERS):
        for t, reason in reasons:
            if t == level:
                print(f'  {t:6} {reason}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
