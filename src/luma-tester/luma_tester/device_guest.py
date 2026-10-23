# SPDX-License-Identifier: Apache-2.0
"""Executed over private control SSH as the guest user; no host dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys


def apply(state):
    os.environ.setdefault('XDG_RUNTIME_DIR','/run/user/'+str(os.getuid()))
    runtime=Path(os.environ['XDG_RUNTIME_DIR'])
    os.environ['DBUS_SESSION_BUS_ADDRESS']='unix:path='+str(runtime/'bus')
    sockets=sorted(p for p in runtime.glob('wayland-*') if p.is_socket())
    if not sockets:raise RuntimeError('The mobile compositor is not ready.')
    os.environ['WAYLAND_DISPLAY']=sockets[0].name
    def command(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,timeout=10)
    def outputs():return json.loads(command(['wlr-randr','--json']))
    output=next(p for p in outputs() if p.get('enabled'))
    old=next(mode for mode in output['modes'] if mode.get('current'))
    name=output['name'];width,height=state['pixels']['width'],state['pixels']['height']
    directory=runtime/'luma-tester';directory.mkdir(mode=0o700,exist_ok=True)
    path=directory/'device.json'
    try:previous=json.loads(path.read_text())
    except (OSError,ValueError):previous={}
    unit='mobi.phosh.OSK.service'
    def service(*args):return command(['systemctl','--user',*args,unit])
    masked=False
    try:
        command(['wlr-randr','--output',name,'--custom-mode',f'{width}x{height}@60',
                 '--scale',str(state['scale']),'--transform','normal'])
        actual=next(p for p in outputs() if p['name']==name)
        mode=next(m for m in actual['modes'] if m.get('current'))
        if (mode['width'],mode['height'])!=(width,height) or abs(actual['scale']-state['scale'])>.01:
            raise RuntimeError('This Mobile image needs the simulator display update before it can resize.')
        # A virtual keyboard is inappropriate for the physical keyboard profile.
        # Restore only a service mask owned by this simulator transition.
        restore=previous.get('restore_osk',False)
        if state['keyboard']=='physical' and previous.get('keyboard')!='physical':
            enabled=subprocess.run(['systemctl','--user','is-enabled',unit],capture_output=True,text=True,timeout=10).stdout.strip()
            if not enabled.startswith('masked'):
                restore=subprocess.run(['systemctl','--user','is-active','--quiet',unit],timeout=10).returncode==0
                service('mask','--runtime','--now');masked=True
        elif state['keyboard']!='physical' and previous.get('osk_mask_owned'):
            service('unmask','--runtime')
            if restore:service('start')
        state=dict(state,output=name,restore_osk=restore,
                   osk_mask_owned=masked or (state['keyboard']=='physical' and previous.get('osk_mask_owned',False)))
        temporary=path.with_suffix('.new');temporary.write_text(json.dumps(state)+'\n');temporary.chmod(0o600);temporary.replace(path)
    except BaseException:
        try:
            command(['wlr-randr','--output',name,'--custom-mode',f"{old['width']}x{old['height']}@{old['refresh']}",
                     '--scale',str(output.get('scale',1)),'--transform',output.get('transform','normal')])
            if masked:
                service('unmask','--runtime')
                if restore:service('start')
        except Exception:pass
        raise
    # File monitors provide durable current state; this is the live notification.
    subprocess.run(['gdbus','emit','--session','--object-path','/org/project_luma/Tester/Device',
        '--signal','org.project_luma.Tester.Device1.Changed',json.dumps(state)],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    return state


if __name__=='__main__':
    print(json.dumps(apply(json.load(sys.stdin))))
