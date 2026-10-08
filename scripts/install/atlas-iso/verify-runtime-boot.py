#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later
"""Assert Lorax installed the payload's exact boot RPM headers, not Fedora substitutes."""
import subprocess
import sys
from pathlib import Path

root, manifest = sys.argv[1:]
for line in Path(manifest).read_text().splitlines():
    nevra, _digest, header = line.split()
    actual = subprocess.check_output(['rpm', '--root', root, '-q', '--qf',
                                     '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH} %{SHA256HEADER}', nevra], text=True)
    if actual != f'{nevra} {header}':
        raise ValueError(f'installer runtime boot-package substitution: {nevra}: {actual}')
print('Installer runtime exact boot RPM headers verified')
