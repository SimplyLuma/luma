#!/usr/bin/python3
"""Install/remove only explicitly supplied disposable local fixtures."""
from pathlib import Path
import subprocess, sys
from luma_installer.inspectors import inspect_package
from luma_installer import workflow
from luma_installer.progress import Transaction, current

root=Path(sys.argv[1])
assert root.name.startswith('luma-install-fixtures-') and root.parent == Path('/var/tmp')
reports=[]
for filename in ('ArrivalFixture.AppImage','One.flatpak','Two.flatpak'):
    report=inspect_package(root/filename)
    assert report.sha256 and report.byte_size>0
    reports.append(report)
print('Static inspection:', [(r.kind,r.title,r.details.get('Runtime')) for r in reports],flush=True)
runtime='org.projectluma.InstallTest.Runtime'
existing=subprocess.run(['flatpak','info','--user',runtime],capture_output=True)
assert existing.returncode!=0, 'Do not overwrite an existing runtime fixture'
subprocess.run(['flatpak','install','--user','--noninteractive','--assumeyes',str(root/'runtime.flatpak')],check=True)
records=[]
try:
    for report in reports:
        events=[];token=current.set(Transaction(events.append))
        try: record=workflow.install(report)
        finally:current.reset(token)
        records.append(record)
        assert events and record['application_id']
        if report.kind=='flatpak':
            result=subprocess.run(['flatpak','run',record['flatpak_id']],capture_output=True,text=True)
            assert result.returncode==0,(result.stdout,result.stderr)
        print('Registered:',record['name'],record['application_id'],flush=True)
    advanced,_=workflow.installed_report(records[1]['application_id'])
    assert 'org.projectluma.InstallTest.Two' in advanced.details['Other apps using the retained runtime'],advanced.details
    print('Shared-runtime facts name the other installed app.',flush=True)
    for record in records:
        workflow.remove(record,keep_data=True)
    records.clear()
    assert (root/'ArrivalFixture.AppImage').is_file()
    print('Three removals passed; original package retained.',flush=True)
finally:
    for record in records:
        try:workflow.remove(record,keep_data=False)
        except Exception as e:print('Cleanup needed:',record['application_id'],e)
    subprocess.run(['flatpak','uninstall','--user','--noninteractive','--assumeyes',runtime],check=False)
