# SPDX-License-Identifier: GPL-3.0-only
"""Derive physical XKB→DOM key names from the retained Chromium source table."""
import argparse
import hashlib
import json
from pathlib import Path
import re

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--license', type=Path, required=True)
args = parser.parse_args()
source = args.source.read_bytes()
text = re.sub(r'//[^\n]*', '', source.decode())
pattern = r'DOM_CODE\(\s*0x[0-9a-f]+,\s*0x[0-9a-f]+,\s*(0x[0-9a-f]+),\s*0x[0-9a-f]+,\s*0x[0-9a-f]+,\s*"([^"]+)"'
keys = {str(int(xkb, 16)): code for xkb, code in re.findall(pattern, text) if int(xkb, 16)}
if keys.get('38') != 'KeyA' or keys.get('36') != 'Enter' or len(keys) < 150:
    raise RuntimeError('Unexpected Chromium physical-key table format')
root = Path(__file__).parent
(root / 'chromium-keycodes.json').write_text(json.dumps({
    'source': 'ui/events/keycodes/dom/dom_code_data.inc',
    'source_sha256': hashlib.sha256(source).hexdigest(),
    'copyright': 'Copyright 2013 The Chromium Authors',
    'license': 'CHROMIUM-LICENSE', 'xkb_to_dom_code': keys}, indent=2) + '\n')
(root / 'CHROMIUM-LICENSE').write_bytes(args.license.read_bytes())
