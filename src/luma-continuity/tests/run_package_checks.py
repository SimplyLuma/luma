"""RPM qualification: missing dependencies and any skipped test fail the build.

`--device OUTPUT` instead runs the native GTK smokes on an installed device
(the Fairphone, a Luma laptop) without touching the live session: a private
bus, a desktop-sized virtual display, the device's own Connect configuration
hidden, a throwaway home, and the handheld device class unset so the windowed
cases are windowed. It never restarts or talks to the running daemons.
"""
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SMOKES=('native_ui_smoke.py','native_message_ui_smoke.py','native_signin_ui_smoke.py')


def device(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    tests=Path(__file__).resolve().parent
    missing=[tool for tool in ('bwrap','dbus-run-session','xvfb-run') if shutil.which(tool) is None]
    if missing:
        print('Device smoke needs:',', '.join(missing),file=sys.stderr);return 2
    failed=[]
    for smoke in SMOKES:
        with tempfile.TemporaryDirectory(prefix='connect-device-smoke-') as home:
            run=Path(home)/'run';run.mkdir(mode=0o700)
            target=output/smoke.removesuffix('.py');target.mkdir(exist_ok=True)
            command=['bwrap','--dev-bind','/','/','--tmpfs','/etc/luma-connect','--bind',home,str(Path.home()),
                'env','-u','LUMA_DEVICE_CLASS',f'HOME={Path.home()}',f'XDG_RUNTIME_DIR={run}',
                'dbus-run-session','--','xvfb-run','-a','-s','-screen 0 1440x1000x24',
                'env','PYTHONNOUSERSITE=1','GSK_RENDERER=cairo','LUMA_CONNECT_PRIVATE_SMOKE=1',
                f'LUMA_CONNECT_SMOKE_OUTPUT={target}',sys.executable,str(tests/smoke)]
            with open(output/(smoke+'.log'),'w') as log:
                code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
            print(('PASS ' if code==0 else 'FAIL ')+smoke)
            if code:failed.append(smoke)
    return 1 if failed else 0


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--device':
        raise SystemExit(device(sys.argv[2]))
    for module in ('authlib','httpx','joserfc','websockets','prairie_apps.messages_backend','prairie_apps.messages_reply'):
        importlib.import_module(module)
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent),pattern='test_*.py')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print('Package qualification must not skip tests:',result.skipped,file=sys.stderr)
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
