#!/usr/bin/python3
"""Run in a graphical user service against only the source-built test APK.

sudo Waydroid shell is diagnostic-only: it checks our fixture's data marker.
Installation and both removals use the shipped Install/runtime/polkit path.
"""
from pathlib import Path
import subprocess
from luma_android.config import load_runtime_config
from luma_android.engine import WaydroidEngine
from luma_installer import workflow
from luma_installer.inspectors import inspect_package

package='org.projectluma.installtest'
engine=WaydroidEngine(load_runtime_config().engine)
assert package not in {a.package for a in engine.applications()}, 'Do not replace an existing app'
source=Path('/var/tmp/LumaInstallTest.apk')
report=inspect_package(source)
record=None

def android(*args, check=True):
    return subprocess.run(['sudo','lxc-attach','-P','/var/lib/waydroid/lxc','-n','waydroid','--clear-env','--',*args],check=check,
                          capture_output=True,text=True,timeout=30)

try:
    record=workflow.install(report)
    assert record['package']==package
    print('Install registered the fixture.',flush=True)
    marker=f'/data/user/0/{package}/files/luma-install-retention-fixture'
    android('/system/bin/sh','-c',f'mkdir -p /data/user/0/{package}/files && printf retained > {marker}')
    assert android('cat',marker).stdout.strip()=='retained'
    workflow.remove(record,keep_data=True);record=None
    marker=f'/data/user/0/{package}/files/luma-install-retention-fixture'
    assert android('cat',marker).stdout.strip()=='retained'
    print('Removal retained the private data and removed the app registration.',flush=True)
    record=workflow.install(report)
    assert android('cat',marker).stdout.strip()=='retained'
    print('A matching signed reinstall recovered the data.',flush=True)
    workflow.remove(record,keep_data=False);record=None
    assert android('test','-e',marker,check=False).returncode != 0
    print('Explicit data removal deleted the marker. Android workflow PASS.',flush=True)
finally:
    if record:
        workflow.remove(record,keep_data=False)
