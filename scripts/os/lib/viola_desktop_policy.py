#!/usr/bin/python3 -B
# SPDX-License-Identifier: Apache-2.0
"""Compose Viola launcher identity without changing personal desktop settings."""
import argparse
from pathlib import Path


def compose(root, signed):
    canonical = root / 'usr/share/applications/com.rhyme.viola.desktop'
    legacy = root / 'usr/share/applications/viola-browser.desktop'
    original = canonical.read_text()
    if 'NoDisplay=true' not in original.splitlines():
        raise ValueError('the native canonical Viola alias is no longer hidden')
    if not legacy.is_file():
        raise ValueError('the legacy Viola alias is missing')
    overrides = root / 'usr/share/luma/desktop-overrides/applications'
    overrides.mkdir(parents=True, exist_ok=True)
    canonical_output = overrides / canonical.name
    if not signed:
        canonical_output.write_text(''.join(line for line in original.splitlines(True)
                                             if not line.startswith('StartupWMClass=')))
        canonical_output.chmod(0o644)
        return [canonical_output]
    if 'Exec=/usr/bin/viola-browser %U' not in original.splitlines():
        raise ValueError('the canonical Viola launcher no longer uses the managed dispatcher')
    dock = root / 'etc/dconf/db/luma.d/00-luma-desktop'
    lines = dock.read_text().splitlines(True)
    favorite_rows = [i for i, line in enumerate(lines) if line.startswith('favorite-apps=')]
    if len(favorite_rows) != 1:
        raise ValueError('the system dock must carry one favorite-apps default')
    index = favorite_rows[0]
    if lines[index].count("'viola-browser.desktop'") != 1:
        raise ValueError('the system dock no longer matches the native Viola policy')
    lines[index] = lines[index].replace("'viola-browser.desktop'", "'com.rhyme.viola.desktop'")
    def entry(text, extra):
        return ''.join(line for line in text.splitlines(True)
                       if not line.startswith(('NoDisplay=', 'StartupWMClass='))) + extra + '\n'
    # The signed application owns its visible artwork independently of the
    # immutable native engine package. Its dedicated icon name also avoids
    # the native package's legacy com.rhyme.viola PNG aliases shadowing it.
    canonical = ''.join(line for line in original.splitlines(True)
                        if not line.startswith('Icon='))
    canonical += 'Icon=com.rhyme.viola.browser\n'
    canonical_output.write_text(entry(canonical, 'StartupWMClass=com.rhyme.viola'))
    alias_output = overrides / legacy.name
    alias_output.write_text(entry(legacy.read_text(), 'NoDisplay=true'))
    dock.write_text(''.join(lines))
    for output in (canonical_output, alias_output):
        output.chmod(0o644)
    return [canonical_output, alias_output]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    args = parser.parse_args()
    from luma_installer.native_app_roles import required
    signed = required('com.rhyme.viola', args.root / 'usr/share/luma/first-party-app-roles.json')
    for output in compose(args.root, signed):
        print(output)
