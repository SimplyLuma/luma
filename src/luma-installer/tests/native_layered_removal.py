#!/usr/bin/python3
"""Remove ONLY the inert owned fixture in an explicitly disposable OSTree VM.

Provision luma-owned-layered-removal-probe with rpm-ostree install --apply-live
first. This gate never installs/removes Firefox, GIMP or another real app.
"""
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

if os.environ.get('LUMA_LAYERED_RPM_DISPOSABLE') != '1' or os.geteuid() != 0:
    raise SystemExit('Refusing: this gate requires an explicitly owned disposable root VM.')
if not Path('/run/ostree-booted').exists():
    raise SystemExit('Refusing: no real booted OSTree deployment.')

from luma_installer import workflow, layered_apps, system_helper

package = 'luma-owned-layered-removal-probe'
desktop = package + '.desktop'
base = Path('/var/tmp/luma-layered-removal-gate')
base.mkdir(exist_ok=True)
personal = base / 'personal-settings'; personal.write_text('must survive uninstall\n')
subprocess.run(['rpm', '-q', '--', package], check=True)
assert layered_apps.identity(desktop) == package
records = []
with patch.object(workflow, 'write_record', side_effect=lambda _, record: records.append(record)):
    record = workflow.resolve_record(desktop)
assert records and record['external_layered'] and record['package_name'] == package
assert record['format'] == 'rpm'
negative = []
for identity, expected in [('org.gnome.Shell.desktop', 'gnome-shell'), (desktop, 'gimp')]:
    try: layered_apps.identity(identity, expected)
    except (OSError, ValueError) as error: negative.append(str(error))
    else: raise AssertionError('Unsafe package identity accepted')
assert len(negative) == 2
before = subprocess.check_output(['rpm-ostree', 'status', '--json'], text=True)
assert system_helper.main(['remove-layered', desktop, package]) == 0
assert subprocess.run(['rpm', '-q', '--', package], capture_output=True).returncode == 1
assert not Path('/usr/share/applications', desktop).exists()
assert personal.read_text() == 'must survive uninstall\n'
receipt = system_helper.SYSTEM_STATE_ROOT / 'receipts' / (layered_apps.receipt_id(desktop, package) + '.json')
assert not receipt.exists(), 'Live absence must reconcile its root receipt'
after = subprocess.check_output(['rpm-ostree', 'status', '--json'], text=True)
result = {'result': 'PASS', 'source_module': str(Path(layered_apps.__file__).resolve()),
          'actual_rpm_owner': package, 'native_adoption': record, 'negative_cases': negative,
          'actual_live_removal': True, 'personal_data_retained': True,
          'before': json.loads(before), 'after': json.loads(after)}
(base / 'NATIVE-GATE.json').write_text(json.dumps(result, indent=2) + '\n')
print('PASS real layered RPM adoption, root owner revalidation, staged/live absence and personal-data retention')
