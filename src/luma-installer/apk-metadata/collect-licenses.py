# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
files = sorted(p for p in Path('vendor').rglob('*') if p.is_file() and
               p.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')))
if not files:
    raise SystemExit('missing dependency notices')
Path('THIRD_PARTY_LICENSES.txt').write_text('\n\n'.join(
    str(p) + '\n' + p.read_text(errors='replace') for p in files))
